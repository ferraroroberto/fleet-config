"""Deterministic e2e/regression test-suite inventory for the `/e2e-audit` skill (fleet-config#406).

A suite like this doesn't just have to be *sufficient* — it also has to stay
*small enough to stay reviewable*. project-scaffolding's `docs/playwright-ui-
testing.md` states the target explicitly: "Keep it small. Target < 15 tests
total. If tempted to add #20, delete two first." This module measures what a
real suite actually looks like against that target, mechanically — file
inventory, raw test-function counts, an optional true pytest node count
(handles `@pytest.mark.parametrize` expansion), near-duplicate-name clusters,
and (when the repo declares a `## UX surface` block, via `ux_surface.py`)
views with no matching test as a coverage-gap signal. Same
deterministic-not-LLM principle as `design_lint.py` / `cert_drift.py`: every
number here is measured, not guessed. The skill's own LLM-judgment layer
decides which clusters/gaps are *real* findings — this module only surfaces
candidates.

Test-directory resolution: a repo's own `## CI expectations` CLAUDE.md block
sometimes already names its e2e surface in prose (e.g. app-launcher: "Its e2e
surface = `app/webapp/`, ... `tests/e2e/`, and static assets."). When present,
the backtick-quoted, test-like paths on that line are used; otherwise this
falls back to `tests/e2e/` — the shared convention project-scaffolding's
playwright-ui-testing.md already establishes fleet-wide, not a path this
module invents per repo.

Subcommand:

  scan <repo-root> [--target N]
      Prints one JSON blob to stdout: test_dirs, per-file inventory (path,
      lines, test names), totals (files, raw_tests, node_count|null), the
      target ratio, near-duplicate-name clusters, and (if the repo has a
      `## UX surface` block) coverage-gap candidates. Always exits 0 — a
      missing test dir, unmeasurable node count, or absent UX-surface block
      are legitimate results (empty inventory / node_count=null / no gaps
      checked), never a crash.

stdlib + the `git`/`gh`-free `git_run` helper + (best-effort) the target
repo's own `.venv` pytest for the true node count.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from ux_surface import parse_ux_surface_block  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

DEFAULT_TEST_DIRS = ["tests/e2e"]
DEFAULT_TARGET = 15

_CI_HEADING = re.compile(r"^##\s+CI expectations\b")
_E2E_SURFACE_LINE_RE = re.compile(r"e2e surface", re.IGNORECASE)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_TEST_DEF_RE = re.compile(r"^\s*def (test_\w+)")


# ---- pure helpers (unit-tested without git/pytest) ------------------------

def find_ci_expectations_block(claude_md: str) -> Optional[str]:
    """Extract the `## CI expectations` section text, or None if absent.

    Stops at the next `## ` heading, same top-anchored-section convention as
    `ux_surface.py`'s block parser.
    """
    lines = claude_md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if _CI_HEADING.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return None
    out: List[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        out.append(line)
    return "\n".join(out)


def extract_backtick_paths(line: str) -> List[str]:
    """All backtick-quoted spans on a line, in order."""
    return _BACKTICK_RE.findall(line)


def filter_test_like_paths(paths: List[str]) -> List[str]:
    """Keep only paths that look like a test location (a `test` path segment).

    A CI-expectations e2e-surface sentence typically names source dirs too
    (`app/webapp/`, `src/session_host*.py`) alongside the actual test dir
    (`tests/e2e/`) — this audit only cares about the latter.
    """
    out = []
    for p in paths:
        segments = re.split(r"[/\\]", p.lower())
        if any("test" in seg for seg in segments):
            out.append(p.rstrip("/"))
    return out


def resolve_test_dirs(claude_md_text: Optional[str]) -> List[str]:
    """Declared test dirs from `## CI expectations`, or the fleet default.

    Falls back to `DEFAULT_TEST_DIRS` (project-scaffolding's `tests/e2e/`
    convention) whenever the block is absent, has no e2e-surface line, or
    that line names no test-like path — never raises.
    """
    if not claude_md_text:
        return list(DEFAULT_TEST_DIRS)
    block = find_ci_expectations_block(claude_md_text)
    if not block:
        return list(DEFAULT_TEST_DIRS)
    for line in block.splitlines():
        if _E2E_SURFACE_LINE_RE.search(line):
            declared = filter_test_like_paths(extract_backtick_paths(line))
            if declared:
                return declared
    return list(DEFAULT_TEST_DIRS)


_NORMALIZE_STRIP_RE = re.compile(r"\d+")
_NORMALIZE_NOISE_WORDS = {
    "test", "regression", "bug", "issue", "case", "scenario", "the", "a", "an", "and",
}


def normalize_test_name(name: str) -> str:
    """Collapse a test name to a token signature for near-duplicate clustering.

    Strips digits (issue numbers, parametrize indices), splits on `_`, drops
    common noise words, sorts the remaining tokens (order-independent — two
    tests asserting the same thing in a different clause order still match),
    and rejoins. Two structurally distinct tests can still collide on a short
    generic name (e.g. `test_smoke`) — that's a candidate for the LLM
    judgment layer to confirm or dismiss, not a guaranteed duplicate.
    """
    stripped = _NORMALIZE_STRIP_RE.sub("", name.lower())
    tokens = [t for t in stripped.split("_") if t and t not in _NORMALIZE_NOISE_WORDS]
    return "_".join(sorted(tokens))


def cluster_candidates(tests: List[Dict[str, str]]) -> List[Dict[str, object]]:
    """Group tests whose normalized name collides across >=2 distinct sites.

    `tests` is a flat list of `{"file": ..., "name": ...}`. Returns one entry
    per colliding signature with >=2 members, each entry `{signature, members:
    [{file, name}, ...]}` — a redundancy *candidate*, not a verdict.
    """
    by_sig: Dict[str, List[Dict[str, str]]] = {}
    for t in tests:
        sig = normalize_test_name(t["name"])
        if not sig:
            continue
        by_sig.setdefault(sig, []).append(t)
    return [
        {"signature": sig, "members": members}
        for sig, members in sorted(by_sig.items())
        if len(members) > 1
    ]


def size_outliers(files: List[Dict[str, object]], factor: float = 3.0) -> List[Dict[str, object]]:
    """Files whose line count exceeds `factor` times the suite's median.

    Informational context for the LLM layer (a large file is not itself a
    finding — it may be one legitimately cohesive view's full coverage), not
    an automatic redundancy claim.
    """
    lines = [int(f["lines"]) for f in files if f.get("lines")]
    if len(lines) < 2:
        return []
    median = statistics.median(lines)
    if median <= 0:
        return []
    return [f for f in files if int(f.get("lines", 0)) > median * factor]


def coverage_gaps(key_views: List[str], all_test_text: str) -> List[str]:
    """Declared `## UX surface` key views with no matching test-name/text hit.

    A crude but conservative substring check: a view like `/settings` is
    considered covered if `settings` appears anywhere in the combined test
    file/function-name text. False negatives (a covered view phrased very
    differently in test names) are possible — the LLM layer should sanity-
    check a reported gap before filing it as a finding, per the skill's
    materiality bar.
    """
    gaps = []
    low_text = all_test_text.lower()
    for view in key_views:
        token = view.strip("/").split("/")[0] or "home"
        if token.lower() not in low_text:
            gaps.append(view)
    return gaps


def target_ratio(total_tests: int, target: int) -> float:
    if target <= 0:
        return 0.0
    return round(total_tests / target, 2)


# ---- IO layer: gather from a repo -----------------------------------------

def _list_files(repo_root: Path, rel_dir: str) -> List[str]:
    """Test files under `rel_dir`, preferring `git ls-files` (respects
    .gitignore); falls back to a plain walk for a non-git tree."""
    abs_dir = repo_root / rel_dir
    if not abs_dir.is_dir():
        return []
    res = git_run.run_git(["-C", str(repo_root), "ls-files", "--", rel_dir])
    candidates: List[str]
    if res.returncode == 0 and res.stdout.strip():
        candidates = [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]
    else:
        candidates = []
        for dirpath, _dirnames, filenames in os.walk(abs_dir):
            for fn in filenames:
                rel = os.path.relpath(os.path.join(dirpath, fn), repo_root).replace("\\", "/")
                candidates.append(rel)
    return sorted(
        c for c in candidates
        if c.endswith(".py") and (c.rsplit("/", 1)[-1].startswith("test_") or c.endswith("_test.py"))
    )


def parse_test_file(repo_root: Path, rel_path: str) -> Dict[str, object]:
    text = (repo_root / rel_path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    names = [m.group(1) for line in lines if (m := _TEST_DEF_RE.match(line))]
    return {"file": rel_path, "lines": len(lines), "tests": names}


def collect_pytest_node_count(repo_root: Path, test_dirs: List[str]) -> Optional[int]:
    """Best-effort true pytest node count (parametrize-expanded), or None.

    Requires the target repo's own `.venv` — never assumes a shared/global
    pytest. Any failure (no venv, pytest not installed, collection error)
    reports None rather than crashing; the caller treats that as "not
    measured", matching the fleet's fail-open convention for optional
    measurements.
    """
    python_exe = repo_root / ".venv" / "Scripts" / "python.exe"
    if not python_exe.is_file():
        return None
    existing_dirs = [d for d in test_dirs if (repo_root / d).is_dir()]
    if not existing_dirs:
        return None
    try:
        res = subprocess.run(
            [str(python_exe), "-m", "pytest", "--collect-only", "-q", *existing_dirs],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^(\d+) tests? collected", res.stdout, re.MULTILINE)
    if not m:
        return None
    return int(m.group(1))


def _load_ux_surface(repo_root: Path) -> List[str]:
    claude_md = repo_root / "CLAUDE.md"
    if not claude_md.is_file():
        return []
    block = parse_ux_surface_block(claude_md.read_text(encoding="utf-8", errors="replace"))
    return list(block["key_views"]) if block else []  # type: ignore[index]


def scan(repo_root: Path, target: int = DEFAULT_TARGET) -> Dict[str, object]:
    claude_md_path = repo_root / "CLAUDE.md"
    claude_md_text = (
        claude_md_path.read_text(encoding="utf-8", errors="replace")
        if claude_md_path.is_file() else None
    )
    test_dirs = resolve_test_dirs(claude_md_text)

    files: List[Dict[str, object]] = []
    all_tests: List[Dict[str, str]] = []
    for d in test_dirs:
        for rel in _list_files(repo_root, d):
            parsed = parse_test_file(repo_root, rel)
            files.append(parsed)
            for name in parsed["tests"]:  # type: ignore[union-attr]
                all_tests.append({"file": rel, "name": name})

    node_count = collect_pytest_node_count(repo_root, test_dirs)
    key_views = _load_ux_surface(repo_root)
    all_test_text = "\n".join(f'{t["file"]} {t["name"]}' for t in all_tests)

    return {
        "test_dirs": test_dirs,
        "files": files,
        "totals": {
            "files": len(files),
            "raw_tests": len(all_tests),
            "node_count": node_count,
        },
        "target": target,
        "ratio": target_ratio(node_count if node_count is not None else len(all_tests), target),
        "clusters": cluster_candidates(all_tests),
        "size_outliers": size_outliers(files),
        "key_views_declared": key_views,
        "coverage_gaps": coverage_gaps(key_views, all_test_text) if key_views else [],
    }


def cmd_scan(repo_root: Path, target: int) -> int:
    print(json.dumps(scan(repo_root, target)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic e2e test-suite inventory for /e2e-audit.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="inventory a repo's e2e test suite")
    p_scan.add_argument("repo", type=Path)
    p_scan.add_argument("--target", type=int, default=DEFAULT_TARGET)

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    return cmd_scan(repo, args.target)


if __name__ == "__main__":
    raise SystemExit(main())
