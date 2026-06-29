"""Local command-output compression for the fleet hook layer.

The module is intentionally heuristic and deterministic. It optimizes the
high-volume shell output surfaces that show up in agent sessions while keeping
diagnostic lines, file paths, and exit-relevant summaries visible.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


TOKEN_DIVISOR = 4
DEFAULT_MAX_LINES = 80
DEFAULT_MAX_CHARS = 12_000
SMALL_OUTPUT_CHARS = 1_800

SIGNAL_RE = re.compile(
    r"("
    r"\b(error|errors|failed|failure|failures|fatal|exception|traceback|assert|warning|warn)\b"
    r"|^\s*(FAILED|ERROR|E\s+|F\s+)"
    r"|\b[A-Za-z0-9_./\\-]+\.py:\d+\b"
    r"|\b[A-Za-z0-9_./\\-]+\.(ts|tsx|js|jsx|go|rs|py|ps1|md):\d+\b"
    r")",
    re.IGNORECASE,
)

SECRET_RE = re.compile(
    r"("
    r"xox[baprs]-[A-Za-z0-9-]{16,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r")"
)

TIMESTAMP_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+|\d{2}:\d{2}:\d{2}(?:\.\d+)?)\b"
)
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
HEX_RE = re.compile(r"\b[0-9a-f]{12,}\b", re.I)
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|m|kb|mb|gb|%)?\b", re.I)
PATH_RE = re.compile(r"([A-Za-z]:)?[/\\][^\s:]+")


@dataclass(frozen=True)
class CompressionResult:
    command: str
    raw: str
    compressed: str
    raw_tokens: int
    compressed_tokens: int
    reduction_pct: float
    line_count: int
    compressed_line_count: int
    duration_ms: float
    raw_key: Optional[str]
    secret_like: bool


@dataclass(frozen=True)
class RewriteDecision:
    should_wrap: bool
    reason: str
    command: str


def estimate_tokens(text: str) -> int:
    """Return a stable, cheap token estimate suitable for before/after deltas."""
    if not text:
        return 0
    return max(1, (len(text) + TOKEN_DIVISOR - 1) // TOKEN_DIVISOR)


def data_dir() -> Path:
    return Path.home() / ".fleet-context-filter"


def cache_raw_output(command: str, raw: str) -> str:
    """Persist raw output locally and return its content-addressed key."""
    stamp = str(time.time_ns())
    digest = hashlib.sha256((command + "\0" + stamp + "\0" + raw).encode("utf-8", "replace")).hexdigest()
    key = digest[:16]
    target_dir = data_dir() / "blobs"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{key}.txt").write_text(raw, encoding="utf-8", errors="replace")
    return key


def append_shadow_log(record: dict[str, Any]) -> None:
    target = data_dir() / "shadow.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def command_base(command: str) -> str:
    stripped = command.strip()
    if not stripped:
        return ""
    if stripped.startswith("& "):
        stripped = stripped[2:].strip()
    quote = stripped[0] if stripped[0] in {"'", '"'} else ""
    if quote:
        end = stripped.find(quote, 1)
        first = stripped[1:end] if end != -1 else stripped[1:]
    else:
        first = stripped.split(maxsplit=1)[0]
    first = first.replace("\\", "/").rsplit("/", 1)[-1]
    if first.lower().endswith(".exe"):
        first = first[:-4]
    return first.lower()


def is_streaming_or_interactive(command: str) -> bool:
    lower = command.lower()
    if re.search(r"\b(tail|less|more)\b.*\s-[a-z]*f\b", lower):
        return True
    if " --follow" in lower or " --watch" in lower or " -w" in lower:
        return True
    if re.search(r"\bdocker\s+(compose\s+)?up\b", lower) and " -d" not in lower and " --detach" not in lower:
        return True
    if re.search(r"\b(npm|pnpm|yarn|bun)\s+run\s+(dev|start|serve)\b", lower):
        return True
    return False


def rewrite_decision(command: str) -> RewriteDecision:
    cmd = command.strip()
    if not cmd:
        return RewriteDecision(False, "empty", command)
    if "fleet_context_filter" in cmd or "context_filter_cli.py" in cmd:
        return RewriteDecision(False, "already wrapped", command)
    if is_streaming_or_interactive(cmd):
        return RewriteDecision(False, "streaming/interactive", command)
    if re.search(r"\b(git\s+push|npm\s+publish|twine\s+upload|docker\s+push)\b", cmd, re.I):
        return RewriteDecision(False, "publish/destructive", command)
    if any(op in cmd for op in (" | ", " > ", " >> ", " < ")):
        return RewriteDecision(False, "pipe/redirect", command)
    if cmd.startswith(("cd ", "set ", "export ", "source ", ". ")):
        return RewriteDecision(False, "shell state mutation", command)

    supported = {
        "git", "gh", "pytest", "python", "npm", "pnpm", "yarn", "bun",
        "rg", "grep", "docker", "kubectl", "ruff", "mypy", "tsc",
        "eslint", "go", "cargo", "dotnet", "uv", "pip", "cat", "tail",
    }
    base = command_base(cmd)
    if base in supported:
        return RewriteDecision(True, "supported command", command)
    return RewriteDecision(False, f"unsupported command: {base or '<unknown>'}", command)


def redact_secret_markers(text: str) -> str:
    return SECRET_RE.sub("[REDACTED_SECRET]", text)


def _template_line(line: str) -> str:
    templated = TIMESTAMP_RE.sub("<time>", line)
    templated = UUID_RE.sub("<uuid>", templated)
    templated = HEX_RE.sub("<hex>", templated)
    templated = PATH_RE.sub("<path>", templated)
    templated = NUMBER_RE.sub("<num>", templated)
    return templated.strip()


def _collapse_repeated_templates(lines: Iterable[str]) -> list[str]:
    collapsed: list[str] = []
    last_template = ""
    last_line = ""
    count = 0

    def flush() -> None:
        nonlocal count, last_line
        if count <= 0:
            return
        if count == 1:
            collapsed.append(last_line)
        else:
            collapsed.append(f"[x{count}] {last_line}")

    for line in lines:
        template = _template_line(line)
        if template and template == last_template:
            count += 1
            continue
        flush()
        last_template = template
        last_line = line
        count = 1
    flush()
    return collapsed


def _dedupe_exact(lines: Iterable[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for line in lines:
        key = line.strip()
        if not key:
            out.append(line)
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= 2 or SIGNAL_RE.search(line):
            out.append(line)
    suppressed = sum(count - 2 for count in seen.values() if count > 2)
    if suppressed:
        out.append(f"[fleet-context-filter: suppressed {suppressed} repeated lines]")
    return out


def _git_status(lines: list[str]) -> Optional[list[str]]:
    interesting: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("On branch", "Your branch", "Changes ", "Untracked ", "Changes not")):
            interesting.append(stripped)
        elif re.match(r"(modified|new file|deleted|renamed|both modified):", stripped):
            interesting.append(stripped)
        elif re.match(r"[ MADRCU?!]{1,2}\s+\S+", line):
            interesting.append(stripped)
    return interesting or None


def _pytest(lines: list[str]) -> Optional[list[str]]:
    keep: list[str] = []
    for line in lines:
        stripped = line.rstrip()
        if (
            " short test summary info " in stripped
            or stripped.startswith(("FAILED ", "ERROR "))
            or stripped.startswith(("E   ", "F   "))
            or "Traceback (most recent call last)" in stripped
            or re.search(r"\b\d+\s+(failed|passed|error|errors|skipped)\b", stripped)
            or re.search(r"\b[A-Za-z0-9_./\\-]+\.py:\d+:", stripped)
        ):
            keep.append(stripped)
    return keep or None


def _npm(lines: list[str]) -> Optional[list[str]]:
    keep: list[str] = []
    pass_count = 0
    has_failure = False
    for line in lines:
        stripped = line.rstrip()
        if re.search(r"\bPASS\s+\S+", stripped):
            pass_count += 1
        if re.search(r"\bFAIL\s+\S+", stripped) or SIGNAL_RE.search(stripped):
            has_failure = True
        if SIGNAL_RE.search(stripped) or re.search(r"\b(Test Suites|Tests|Snapshots|Time):", stripped):
            keep.append(stripped)
        elif re.search(r"\b(PASS|FAIL)\s+\S+", stripped):
            keep.append(stripped)
    if pass_count and not has_failure:
        summary = [f"all visible test suites passed ({pass_count} PASS lines)"]
        summary.extend(line for line in keep if re.search(r"\b(Test Suites|Tests|Snapshots|Time):", line))
        return summary
    return keep or None


def command_specific_lines(command: str, lines: list[str]) -> Optional[list[str]]:
    base = command_base(command)
    lower = command.lower()
    if base == "git" and " status" in f" {lower} ":
        return _git_status(lines)
    if base == "pytest" or " pytest" in f" {lower} ":
        return _pytest(lines)
    if base in {"npm", "pnpm", "yarn", "bun"} and re.search(r"\b(test|run\s+test)\b", lower):
        return _npm(lines)
    if base in {"cat", "tail"}:
        collapsed = _collapse_repeated_templates(lines)
        signal = [line.rstrip() for line in collapsed if SIGNAL_RE.search(line)]
        if signal:
            return signal
        if len(collapsed) < len(lines):
            return collapsed[:20]
    return None


def _looks_json(text: str) -> bool:
    stripped = text.strip()
    return stripped[:1] in {"{", "["}


def _json_summary(text: str) -> Optional[str]:
    if not _looks_json(text):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, list):
        keys = sorted({k for item in parsed[:25] if isinstance(item, dict) for k in item.keys()})
        if len(parsed) < 8 and len(text) <= SMALL_OUTPUT_CHARS:
            return None
        return f"JSON array: {len(parsed)} items; keys: {', '.join(keys[:20])}"
    if isinstance(parsed, dict):
        keys = sorted(parsed.keys())
        if len(keys) < 12 and len(text) <= SMALL_OUTPUT_CHARS:
            return None
        preview = ", ".join(str(k) for k in keys[:30])
        return f"JSON object: {len(keys)} top-level keys; keys: {preview}"
    return f"JSON {type(parsed).__name__}"


def compress_output(
    command: str,
    raw: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_chars: int = DEFAULT_MAX_CHARS,
    cache_raw: bool = False,
) -> CompressionResult:
    start = time.perf_counter()
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    secret_like = bool(SECRET_RE.search(normalized))
    safe_raw = redact_secret_markers(normalized)
    raw_tokens = estimate_tokens(normalized)
    lines = safe_raw.splitlines()

    json_summary = _json_summary(safe_raw)
    if json_summary:
        candidate_lines = [json_summary]
    elif len(safe_raw) <= SMALL_OUTPUT_CHARS and len(lines) <= max_lines:
        candidate_lines = lines
    else:
        candidate_lines = command_specific_lines(command, lines) or []
        if not candidate_lines:
            signal = [line.rstrip() for line in lines if SIGNAL_RE.search(line)]
            head = [line.rstrip() for line in lines[: min(12, len(lines))]]
            tail = [line.rstrip() for line in lines[-min(20, len(lines)) :]] if len(lines) > 12 else []
            candidate_lines = head + ["[... middle omitted ...]"] + signal + tail

    candidate_lines = _collapse_repeated_templates(_dedupe_exact(candidate_lines))
    if len(candidate_lines) > max_lines:
        tail_budget = max(8, max_lines // 4)
        candidate_lines = (
            candidate_lines[: max_lines - tail_budget - 1]
            + [f"[fleet-context-filter: omitted {len(candidate_lines) - max_lines + 1} low-signal lines]"]
            + candidate_lines[-tail_budget:]
        )

    compressed = "\n".join(candidate_lines).strip()
    if len(compressed) > max_chars:
        compressed = compressed[:max_chars].rstrip() + "\n[fleet-context-filter: truncated at char budget]"

    if not compressed:
        compressed = "[fleet-context-filter: command produced no output]"

    raw_key = None
    if cache_raw and not secret_like and normalized:
        raw_key = cache_raw_output(command, normalized)

    compressed_tokens = estimate_tokens(compressed)
    if compressed_tokens > raw_tokens:
        compressed = safe_raw
        compressed_tokens = estimate_tokens(compressed)

    duration_ms = (time.perf_counter() - start) * 1000
    reduction = 0.0 if raw_tokens == 0 else (raw_tokens - compressed_tokens) / raw_tokens * 100
    return CompressionResult(
        command=command,
        raw=normalized,
        compressed=compressed,
        raw_tokens=raw_tokens,
        compressed_tokens=compressed_tokens,
        reduction_pct=reduction,
        line_count=len(lines),
        compressed_line_count=len(compressed.splitlines()),
        duration_ms=duration_ms,
        raw_key=raw_key,
        secret_like=secret_like,
    )
