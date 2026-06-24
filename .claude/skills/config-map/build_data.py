"""Introspect the fleet's cross-agent config surface into the config-map data.

``architecture/config.data.js`` is the file the renderer (``config-map.html``) and
the drift test (``tests/run_acceptance.py``) read. It is *generated* — never
hand-edited. Unlike ``/system-map`` (which aggregates self-describing per-repo
``.fleet.toml`` cards), almost all config is centralized in ``fleet-config``, so
this builder *derives* the inventory by introspection and overlays it on a thin
hand-maintained residual:

* ``architecture/config.residual.json`` — holds only what cannot be derived: the
  agent columns, the capability-matrix row structure (link-backed cells are
  derived from ``install.ps1``; non-derivable cells carry an ``annot``), the
  universal-skill scope set, the project-wired hooks, and the conventions prose.
* derived at build time:
  - per-agent matrix wiring → parsed from ``install.ps1`` ``$Items`` + which 5
    hooks Codex wires in ``codex-hooks.json``;
  - universal skills → ``skills/*/SKILL.md`` (name + description);
  - fleet-orchestration skills → ``.claude/skills/*/SKILL.md`` + ``run-weekly.bat``
    presence (the scheduled flag);
  - hooks → ``hooks/*.py`` (purpose from the module docstring; blocking from a
    ``block(`` / ``exit(2)`` call) + wiring from ``settings.template.json`` /
    ``codex-hooks.json``;
  - repo-specific skills → a git sweep of each fleet repo's committed
    ``.claude/skills`` (``git ls-tree``), exactly how ``/system-map`` reads
    per-repo ``.fleet.toml`` from the committed default branch;
  - convention coverage → committed ``CLAUDE.md`` / ``.fleet.toml`` per repo.

Because the per-repo data is read from each repo's committed default branch, the
output is deterministic and the drift test can assert ``config.data.js`` is
exactly what this script regenerates — same anti-staleness contract as
``/system-map``.

By construction this reads only *wiring and structure*, never the live
``~/.claude/settings.json``, so no secret value can ever enter the dataset.

Usage::

    py .claude/skills/config-map/build_data.py            # regenerate config.data.js
    py .claude/skills/config-map/build_data.py --check     # exit 1 if the file is stale
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_JS = REPO_ROOT / "architecture" / "config.data.js"
RESIDUAL = REPO_ROOT / "architecture" / "config.residual.json"
PROJECTS_TOML = REPO_ROOT / "hooks" / "projects.toml"
INSTALL_PS1 = REPO_ROOT / "install.ps1"
SETTINGS_TEMPLATE = REPO_ROOT / "settings.template.json"
CODEX_HOOKS = REPO_ROOT / "codex-hooks.json"

# install.ps1 link `base` → (agent key it serves, home-path display prefix). The
# `agents` base (the shared ~/.agents/skills junction) serves BOTH Codex and Pi.
BASE_AGENTS = {
    "claude": [("claude", "~/.claude/")],
    "agents": [("codex", "~/.agents/"), ("pi", "~/.agents/")],
    "codex": [("codex", "~/.codex/")],
    "pi": [("pi", "~/.pi/agent/")],
    "copilot": [("copilot", "~/.copilot/")],
}

HEADER = """\
// config.data.js — the data behind the fleet config & convention map. GENERATED
// by .claude/skills/config-map/build_data.py — DO NOT hand-edit. Edit each source
// it derives from (install.ps1, skills/, hooks/, settings.template.json,
// codex-hooks.json, each repo's .claude/skills) or the hand-maintained
// architecture/config.residual.json, then regenerate:
//   py .claude/skills/config-map/build_data.py
// Loaded as plain JS (works under file://, no CORS): sets window.CONFIG.
// The body is strict JSON so Python (build_data.py + the drift test in
// tests/run_acceptance.py) can read it too: strip "window.CONFIG =" + trailing ";".
// Contains only wiring/structure — never a secret value from settings.json.
"""


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------

def load_residual(path: Path = RESIDUAL) -> dict:
    """Parse the hand-maintained residual (strict JSON)."""
    return json.loads(path.read_text(encoding="utf-8"))


def _esc(text: str) -> str:
    """Escape for innerHTML injection (only & < > — quotes are safe in a text node)."""
    return html.escape(text, quote=False)


def first_sentence(text: str, cap: int = 130) -> str:
    """Trim a long description to its first sentence, capped, for a tidy card."""
    t = " ".join(text.split())
    idx = t.find(". ")
    if idx != -1:
        t = t[: idx + 1]
    if len(t) > cap:
        t = t[: cap - 1].rstrip() + "…"
    return t


def _frontmatter_field(skill_md: Path, field: str) -> str | None:
    """Read a single ``field:`` value from a SKILL.md YAML frontmatter block."""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text
    m = re.search(rf"^{re.escape(field)}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip() if m else None


def _docstring_first_line(py: Path, cap: int = 120) -> str:
    """First non-empty line of a module's ``\"\"\"docstring\"\"\"`` (its purpose)."""
    try:
        text = py.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'"""(.*?)"""', text, re.DOTALL)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        line = line.strip()
        if line:
            return first_sentence(line, cap)
    return ""


def _module_blocks(py: Path) -> bool:
    """Heuristic: does this hook ever block? (calls ``block(`` or exits 2)."""
    try:
        text = py.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(re.search(r"\bblock\s*\(", text) or re.search(r"exit\s*\(\s*2\s*\)", text))


# ---------------------------------------------------------------------------
# fleet repos (same definition as /system-map) + committed-state git reads
# ---------------------------------------------------------------------------

def fleet_repos(projects_toml: Path = PROJECTS_TOML) -> dict[str, Path]:
    """``{repo_name: repo_dir}`` — every ``[<name>]`` with a ``cwd_prefix`` minus
    ``[global] architecture_ignore`` (the same fleet set ``/system-map`` uses)."""
    toml = tomllib.loads(projects_toml.read_text(encoding="utf-8"))
    ignore = set(toml.get("global", {}).get("architecture_ignore", []))
    return {
        name: Path(tbl["cwd_prefix"])
        for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
        and name not in ignore
    }


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True)


def _default_ref(repo_dir: Path) -> str | None:
    """The committed default branch ref to read a repo's state from."""
    head = _git(repo_dir, "rev-parse", "--abbrev-ref", "origin/HEAD")
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.decode("utf-8", "replace").strip()
    for cand in ("origin/main", "origin/master", "main", "master"):
        if _git(repo_dir, "rev-parse", "--verify", "--quiet", cand).returncode == 0:
            return cand
    return None


def _committed_skills(repo_dir: Path, ref: str) -> list[str]:
    """The repo's committed ``.claude/skills`` child names (skills), sorted."""
    shown = _git(repo_dir, "ls-tree", "--name-only", f"{ref}:.claude/skills")
    if shown.returncode != 0:
        return []
    names = [
        Path(line.strip()).name
        for line in shown.stdout.decode("utf-8", "replace").splitlines()
        if line.strip()
    ]
    return sorted(names)


def _committed_has(repo_dir: Path, ref: str, path: str) -> bool:
    return _git(repo_dir, "cat-file", "-e", f"{ref}:{path}").returncode == 0


# ---------------------------------------------------------------------------
# install.ps1 link table → the per-agent matrix
# ---------------------------------------------------------------------------

def parse_install_links(install_ps1: Path = INSTALL_PS1) -> list[dict]:
    """Parse the ``$Items`` array in install.ps1 into ``{source, target, base}``.

    Each entry is a ``@{ kind = '…'; source = '…'; target = '…'; base = '…' }``
    PowerShell hashtable line; ``base`` defaults to ``claude`` when absent.
    """
    text = install_ps1.read_text(encoding="utf-8")
    links: list[dict] = []
    for m in re.finditer(r"@\{([^}]*)\}", text):
        body = m.group(1)
        if "source" not in body or "target" not in body:
            continue
        src = re.search(r"source\s*=\s*'([^']*)'", body)
        tgt = re.search(r"target\s*=\s*'([^']*)'", body)
        base = re.search(r"base\s*=\s*'([^']*)'", body)
        if not (src and tgt):
            continue
        links.append({
            "source": src.group(1),
            "target": tgt.group(1),
            "base": base.group(1) if base else "claude",
        })
    return links


def _cell_for(agent: str, src: str, links: list[dict]) -> str | None:
    """The home path a given ``src`` is linked to for ``agent`` (or ``None``)."""
    for link in links:
        if link["source"] != src:
            continue
        for akey, prefix in BASE_AGENTS.get(link["base"], []):
            if akey == agent:
                return prefix + link["target"]
    return None


def build_matrix(residual: dict, links: list[dict]) -> list[dict]:
    """Resolve each matrix row: derived link cells, residual ``annot`` overrides."""
    agents = [a["key"] for a in residual["agents"]]
    rows = []
    for row in residual["matrix_rows"]:
        annot = row.get("annot", {})
        cells = {}
        for agent in agents:
            if agent in annot:
                cells[agent] = annot[agent]
            elif "src" in row:
                cells[agent] = _cell_for(agent, row["src"], links) or "—"
            else:
                cells[agent] = "—"
        rows.append({"cls": row["cls"], "sub": row.get("sub", ""), "cells": cells})
    return rows


# ---------------------------------------------------------------------------
# skills + hooks inventory (derived from the fleet-config working tree)
# ---------------------------------------------------------------------------

def _scan_skill_dir(skills_dir: Path) -> list[tuple[str, Path]]:
    """``(name, dir)`` for each real skill folder (skips ``_``-prefixed helpers)."""
    if not skills_dir.is_dir():
        return []
    return sorted(
        (d.name, d)
        for d in skills_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_") and (d / "SKILL.md").is_file()
    )


def universal_skills(residual: dict) -> list[dict]:
    """The cross-agent skills in ``skills/`` (junctioned into every agent)."""
    fleet_wide = set(residual.get("fleet_wide_skills", []))
    out = []
    for name, d in _scan_skill_dir(REPO_ROOT / "skills"):
        ds = _frontmatter_field(d / "SKILL.md", "description") or ""
        out.append({
            "nm": name,
            "ds": _esc(first_sentence(ds)),
            "scope": "fleet" if name in fleet_wide else "repo",
        })
    return out


def fleet_skills() -> list[dict]:
    """fleet-config's own ``.claude/skills`` (Claude-only, fleet-scope); the
    scheduled flag is whether a ``run-weekly.bat`` sits in the skill folder."""
    out = []
    for name, d in _scan_skill_dir(REPO_ROOT / ".claude" / "skills"):
        ds = _frontmatter_field(d / "SKILL.md", "description") or ""
        out.append({
            "nm": name,
            "ds": _esc(first_sentence(ds)),
            "sched": (d / "run-weekly.bat").is_file(),
        })
    return out


def repo_skills(repos: dict[str, Path]) -> list[dict]:
    """Repo-specific skills across the fleet (committed ``.claude/skills``),
    excluding fleet-config itself (its skills are the fleet-orchestration set)."""
    out = []
    for name, repo_dir in sorted(repos.items()):
        if name == "fleet-config":
            continue
        ref = _default_ref(repo_dir)
        if ref is None:
            continue
        items = _committed_skills(repo_dir, ref)
        if items:
            out.append({"repo": name, "items": items})
    return out


def _parse_wiring(path: Path) -> list[tuple[str, str, str]]:
    """``(hook_name, event, matcher)`` for every wired hook in a settings file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    wiring = []
    for event, blocks in data.get("hooks", {}).items():
        for block in blocks:
            matcher = block.get("matcher", "")
            for hook in block.get("hooks", []):
                m = re.search(r"-Hook\s+(\w+)", hook.get("command", ""))
                if m:
                    wiring.append((m.group(1), event, matcher))
    return wiring


def hooks_inventory(residual: dict) -> tuple[list[dict], list[dict]]:
    """``(hooks, helpers)``.

    ``hooks`` = every globally-wired hook (from settings.template.json, in order)
    plus the residual's project-wired extras; each carries its event, whether it
    blocks, its reach (Codex wires a subset via codex-hooks.json), and purpose.
    ``helpers`` = the remaining ``hooks/*.py`` (shared libs, no wiring).
    """
    claude_wiring = _parse_wiring(SETTINGS_TEMPLATE)
    codex_names = {n for n, _, _ in _parse_wiring(CODEX_HOOKS)}
    hooks_dir = REPO_ROOT / "hooks"

    def _event(event: str, matcher: str) -> str:
        m = matcher.replace("|", "·")
        return f"{event} · {m}" if m else event

    hooks: list[dict] = []
    seen: set[str] = set()
    for name, event, matcher in claude_wiring:
        if name in seen:
            continue
        seen.add(name)
        py = hooks_dir / f"{name}.py"
        hooks.append({
            "nm": name,
            "ev": _event(event, matcher),
            "block": _module_blocks(py),
            "reach": "Claude + Codex" if name in codex_names else "Claude only",
            "ds": _esc(_docstring_first_line(py)),
        })

    for extra in residual.get("hooks_extra", []):
        name = extra["nm"]
        seen.add(name)
        py = hooks_dir / f"{name}.py"
        hooks.append({
            "nm": name,
            "ev": extra["ev"],
            "block": _module_blocks(py),
            "reach": "Claude + Codex" if name in codex_names else "Claude only",
            "ds": _esc(_docstring_first_line(py)),
        })

    helpers: list[dict] = []
    for py in sorted(hooks_dir.glob("*.py")):
        if py.stem in seen:
            continue
        helpers.append({"nm": py.stem, "ds": _esc(_docstring_first_line(py))})

    return hooks, helpers


def coverage(repos: dict[str, Path]) -> dict:
    """Committed ``CLAUDE.md`` / ``.fleet.toml`` coverage over the fleet set."""
    total = len(repos)
    claude_md = 0
    fleet_toml = 0
    for repo_dir in repos.values():
        ref = _default_ref(repo_dir)
        if ref is None:
            continue
        claude_md += _committed_has(repo_dir, ref, "CLAUDE.md")
        fleet_toml += _committed_has(repo_dir, ref, ".fleet.toml")
    return {
        "total": total,
        "claude_md": f"{claude_md}/{total}",
        "fleet_toml": f"{fleet_toml}/{total}",
    }


# ---------------------------------------------------------------------------
# assemble + serialize
# ---------------------------------------------------------------------------

def build(residual: dict) -> dict:
    links = parse_install_links()
    repos = fleet_repos()
    hooks, helpers = hooks_inventory(residual)
    return {
        "agents": residual["agents"],
        "matrix": build_matrix(residual, links),
        "skills_universal": universal_skills(residual),
        "skills_fleet": fleet_skills(),
        "skills_repo": repo_skills(repos),
        "hooks": hooks,
        "hooks_helpers": helpers,
        "conventions": residual.get("conventions", []),
        "coverage": coverage(repos),
        "principles": residual.get("principles", []),
    }


def serialize(data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return HEADER + "window.CONFIG = " + body + ";\n"


def regenerate() -> str:
    return serialize(build(load_residual()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Introspect the fleet config surface into config.data.js.")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if config.data.js is stale (do not write)")
    args = ap.parse_args(argv)

    try:
        rendered = regenerate()
    except (ValueError, KeyError, OSError) as exc:
        print(f"build_data: {exc}", file=sys.stderr)
        return 1

    current = DATA_JS.read_text(encoding="utf-8") if DATA_JS.is_file() else ""
    if args.check:
        if rendered != current:
            print("build_data: config.data.js is STALE — run "
                  "`py .claude/skills/config-map/build_data.py` and commit.", file=sys.stderr)
            return 1
        print("build_data: config.data.js is up to date.")
        return 0

    if rendered != current:
        DATA_JS.write_text(rendered, encoding="utf-8")
        print(f"build_data: regenerated {DATA_JS.name}.")
    else:
        print(f"build_data: {DATA_JS.name} already up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
