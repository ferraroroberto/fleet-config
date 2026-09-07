"""Reconcile fleet-owned Codex model policy settings without replacing user config.

The command is intentionally opt-in.  It validates the checked-in catalog,
role layers, and installed Codex CLI in a disposable ``CODEX_HOME`` before an
atomic write can touch ``~/.codex/config.toml``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path


MAIN_MODEL = "gpt-5.6-sol"
MAIN_EFFORT = "medium"
CATALOG_MODELS = ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra")
ROLE_SPECS = {
    "easy": ("gpt-5.6-luna", "xhigh"),
    "normal": ("gpt-5.6-terra", "high"),
    "hard": ("gpt-5.6-sol", "high"),
}
_TABLE_RE = re.compile(r"^[ \t]*\[([^\]]+)\][ \t]*(?:#.*)?(?:\r?\n)?$")
_ASSIGNMENT_RE = re.compile(r"^([ \t]*)([A-Za-z0-9_.-]+)[ \t]*=")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class PolicyError(ValueError):
    """Raised when the policy cannot be safely validated or applied."""


def default_config_path() -> Path:
    """Return the user-level Codex config path, respecting ``CODEX_HOME``."""

    home = os.environ.get("CODEX_HOME")
    return Path(home) / "config.toml" if home else Path.home() / ".codex" / "config.toml"


def default_policy_root() -> Path:
    """Return the checked-in policy asset directory."""

    return Path(__file__).resolve().parent / "codex"


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _table_ranges(lines: list[str]) -> list[tuple[str | None, int, int]]:
    """Return TOML table ranges, including the top-level range."""

    headers = [(index, match.group(1).strip()) for index, line in enumerate(lines) if (match := _TABLE_RE.match(line))]
    ranges: list[tuple[str | None, int, int]] = [(None, 0, headers[0][0] if headers else len(lines))]
    for index, (start, name) in enumerate(headers):
        end = headers[index + 1][0] if index + 1 < len(headers) else len(lines)
        ranges.append((name, start, end))
    return ranges


def _merge_table(text: str, table: str | None, values: dict[str, str]) -> str:
    """Update one owned table, preserving every unrelated line byte-for-byte."""

    lines = text.splitlines(keepends=True)
    ranges = _table_ranges(lines)
    target = next((item for item in ranges if item[0] == table), None)
    newline = _line_ending(text)
    if target is None:
        if table is None:
            raise PolicyError("top-level TOML range is unavailable")
        suffix = "" if not text or text.endswith(("\n", "\r")) else newline
        addition = f"{suffix}[{table}]{newline}" + "".join(f"{key} = {value}{newline}" for key, value in values.items())
        return text + addition

    _, start, end = target
    body_start = start if table is None else start + 1
    output: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if body_start <= index < end and (match := _ASSIGNMENT_RE.match(line)) and match.group(2) in values:
            key = match.group(2)
            if key not in seen:
                comment_start = line.find("#")
                comment = line[comment_start:].rstrip("\r\n") if comment_start >= 0 else ""
                suffix = f" {comment}" if comment else ""
                line_end = "\r\n" if line.endswith("\r\n") else "\n"
                output.append(f"{match.group(1)}{key} = {values[key]}{suffix}{line_end}")
                seen.add(key)
            continue
        output.append(line)
        if index == end - 1:
            output.extend(f"{key} = {value}{newline}" for key, value in values.items() if key not in seen)
    if body_start == end:
        insert_at = start + (0 if table is None else 1)
        additions = [f"{key} = {value}{newline}" for key, value in values.items() if key not in seen]
        output[insert_at:insert_at] = additions
    return "".join(output)


def merge_policy(text: str, catalog_path: Path, policy_root: Path) -> tuple[str, tuple[str, ...]]:
    """Return config text reconciled with fleet-owned model-policy keys."""

    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"config.toml is not valid TOML: {exc}") from exc
    catalog = catalog_path.resolve().as_posix()
    merged = _merge_table(text, None, {
        "model": json.dumps(MAIN_MODEL),
        "model_reasoning_effort": json.dumps(MAIN_EFFORT),
        "model_catalog_json": json.dumps(catalog),
    })
    merged = _merge_table(merged, "agents", {
        "default_subagent_model": json.dumps("gpt-5.6-terra"),
        "default_subagent_reasoning_effort": json.dumps("high"),
    })
    for role in ROLE_SPECS:
        role_path = (policy_root / "agents" / f"{role}.toml").resolve().as_posix()
        merged = _merge_table(merged, f"agents.{role}", {
            "description": json.dumps(f"Fleet {role} tier; model and effort are versioned."),
            "config_file": json.dumps(role_path),
        })
    try:
        parsed = tomllib.loads(merged)
    except tomllib.TOMLDecodeError as exc:
        raise PolicyError(f"updated config.toml is not valid TOML: {exc}") from exc
    if parsed.get("model") != MAIN_MODEL or parsed.get("model_reasoning_effort") != MAIN_EFFORT:
        raise PolicyError("main-thread model policy did not resolve correctly")
    if tuple(parsed.get("agents", {}).get(role, {}).get("config_file", "") for role in ROLE_SPECS) != tuple(
        (policy_root / "agents" / f"{role}.toml").resolve().as_posix() for role in ROLE_SPECS
    ):
        raise PolicyError("custom role config layers did not resolve correctly")
    changed = () if merged == text else ("model-policy",)
    return merged, changed


def validate_assets(policy_root: Path) -> None:
    """Validate the versioned catalog and each role layer before any write."""

    try:
        catalog = json.loads((policy_root / "model_catalog.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"model catalog is unreadable: {exc}") from exc
    if not isinstance(catalog, dict) or tuple(catalog.get("slugs", ())) != CATALOG_MODELS:
        raise PolicyError("model policy must select exactly Luna, Terra, Sol, and Astra in that order")
    for role, (model, effort) in ROLE_SPECS.items():
        path = policy_root / "agents" / f"{role}.toml"
        try:
            layer = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PolicyError(f"{role} role layer is unreadable: {exc}") from exc
        if (layer.get("model"), layer.get("model_reasoning_effort")) != (model, effort):
            raise PolicyError(f"{role} role layer does not match the fleet policy")


def build_catalog(executable: str) -> str:
    """Filter the installed bundled catalog while retaining required model metadata."""

    binary = shutil.which(executable) or executable
    result = subprocess.run(
        [binary, "debug", "models", "--bundled"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise PolicyError(f"unable to read Codex's bundled model catalog: {result.stderr.strip() or result.stdout.strip()}")
    try:
        bundled = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PolicyError("Codex did not return its bundled model catalog") from exc
    available = {model.get("slug"): model for model in bundled.get("models", []) if isinstance(model, dict)}
    if any(slug not in available for slug in CATALOG_MODELS):
        raise PolicyError("installed Codex does not advertise every configured fleet model")
    catalog = {"models": [available[slug] for slug in CATALOG_MODELS]}
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def validate_cli(config_text: str, catalog_text: str, executable: str) -> None:
    """Ask installed Codex to load the generated config in an isolated home."""

    binary = shutil.which(executable) or executable
    with tempfile.TemporaryDirectory(prefix="fleet-codex-policy-") as directory:
        home = Path(directory)
        temporary_catalog = home / "model_catalog.json"
        temporary_catalog.write_text(catalog_text, encoding="utf-8", newline="")
        rendered_config = re.sub(
            r'(?m)^model_catalog_json\s*=\s*"[^"]*"',
            f'model_catalog_json = {json.dumps(temporary_catalog.as_posix())}',
            config_text,
            count=1,
        )
        (home / "config.toml").write_text(rendered_config, encoding="utf-8", newline="")
        environment = {**os.environ, "CODEX_HOME": str(home)}
        command = [binary, "debug", "models"]
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=environment, creationflags=NO_WINDOW,
        )
        if result.returncode != 0:
            raise PolicyError(f"installed Codex rejected the policy: {result.stderr.strip() or result.stdout.strip()}")
        try:
            rendered = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PolicyError("installed Codex did not return a model catalog") from exc
        models = rendered.get("models", [])
        slugs = tuple(model.get("slug") for model in models if isinstance(model, dict))
        if slugs != CATALOG_MODELS:
            raise PolicyError(f"installed Codex catalog differs from policy: {slugs}")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(text)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def configure(config_path: Path, policy_root: Path, executable: str, *, apply: bool) -> str:
    """Validate, then check or atomically apply the user-level policy."""

    try:
        if config_path.exists():
            with config_path.open("r", encoding="utf-8", newline="") as handle:
                original = handle.read()
        else:
            original = ""
    except OSError as exc:
        raise PolicyError(f"config.toml is unreadable: {exc}") from exc
    validate_assets(policy_root)
    catalog_path = config_path.parent / "fleet-config" / "model_catalog.json"
    catalog_text = build_catalog(executable)
    updated, _ = merge_policy(original, catalog_path, policy_root)
    validate_cli(updated, catalog_text, executable)
    if updated == original:
        return "unchanged"
    if apply:
        _atomic_write(catalog_path, catalog_text)
        _atomic_write(config_path, updated)
        return "updated"
    return "update-needed"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--apply", action="store_true", help="atomically reconcile the user config")
    action.add_argument("--check", action="store_true", help="validate and report whether reconciliation is needed")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--policy-root", type=Path, default=default_policy_root())
    parser.add_argument("--codex", default="codex", help="Codex executable used for disposable validation")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        state = configure(args.config, args.policy_root.resolve(), args.codex, apply=args.apply)
        print(f"CODEX_MODEL_POLICY status={state} config={args.config}")
        return 0
    except (OSError, PolicyError) as exc:
        print(f"CODEX_MODEL_POLICY status=error detail={exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
