"""Deterministic e2e/regression test-suite inventory for the `/e2e-audit` skill (fleet-config#406).

A suite like this doesn't just have to be *sufficient* — it also has to stay
*small enough to stay reviewable*. project-scaffolding's `docs/playwright-ui-
testing.md` states the target explicitly: "Keep it small. Target < 15 tests
total. If tempted to add #20, delete two first." This module measures what a
real suite actually looks like against that target, mechanically — file
inventory, raw test-function counts, an optional true pytest node count
(handles `@pytest.mark.parametrize` expansion), and two independent redundancy
detectors — near-duplicate *name* clusters, and same-file tests sweeping the
same parametrize *matrix* (differently-named tests over one shared `MATRIX`
collide on neither name nor assertion text, so name clustering alone reported
a confident zero over 32 collected nodes; fleet-config#602) — plus, when the
repo declares a `## UX surface` block (via `ux_surface.py`),
views with no matching test as a coverage-gap signal. Same
deterministic-not-LLM principle as `design_lint/` / `cert_drift.py`: every
number here is measured, not guessed. The skill's own LLM-judgment layer
decides which clusters/gaps are *real* findings — this module only surfaces
candidates.

Test-directory resolution: a repo's own `## CI expectations` CLAUDE.md block
sometimes already names its e2e surface in prose (e.g. app-launcher: "Its e2e
surface = `app/webapp/`, ... `tests/e2e/`, and static assets."). When present,
the backtick-quoted, test-like paths on that line are used; otherwise this
falls back to `tests/e2e/` — the shared convention project-scaffolding's
playwright-ui-testing.md already establishes fleet-wide, not a path this
module invents per repo. Headings inside a fenced code block are ignored, so a
repo that *documents* the block template verbatim is not mistaken for one that
declares it (fleet-config#602).

Subcommands:

  scan <repo-root> [--target N]
      Prints one JSON blob to stdout: test_dirs, `test_dirs_resolved` +
      `test_dirs_missing`, per-file inventory (path, lines, test names,
      parametrize signatures), totals (files, raw_tests, node_count|null), the
      target ratio, near-duplicate-name `clusters`, shared-matrix
      `matrix_clusters`, and (if the repo has a `## UX surface` block)
      coverage-gap candidates. Always exits 0 — a missing test dir,
      unmeasurable node count, or absent UX-surface block are legitimate
      results (empty inventory / node_count=null / no gaps checked), never a
      crash. `test_dirs_resolved: false` is the one result a caller must not
      read as "no tests": it means the scan had nowhere to look.

  budget <repo-root>
      The feature-driven trigger for `/e2e-audit` (fleet-config#901): `/e2e`
      runs this on every finish. Prints `E2E_BUDGET=over|within|unmeasured|
      no-suite` plus `E2E_BUDGET_COUNT`, `E2E_BUDGET_COUNT_KIND`,
      `E2E_BUDGET_LIMIT`, `E2E_BUDGET_SOURCE` and `E2E_BUDGET_REASON`. The
      limit is `.fleet.toml` `[e2e] test_budget` when it is a positive integer
      (a per-repo ratchet), else project-scaffolding's 15. An unmeasurable
      count is `unmeasured`, never `within`. Always exits 0. Assumes the
      caller already established that a suite exists (`/e2e` runs it only
      after `e2e_route.py probe` prints `SUITE=present`): test dirs that
      resolve to nothing print `unmeasured`, not `no-suite`.

  timing <repo-root> [--log <path>]
  failures <repo-root> [--log <path>] [--prs N] [--no-gh]
      Where the suite's time goes and what its failures were (fleet-config#1018),
      read from the last completed gate run's progress log (`.fleet.toml`
      `[e2e] progress_log`, else `[e2e] junit_xml`, else `--log`). JSON to
      stdout, always exit 0; no source, no completed run or no e2e node is
      `status: unknown` with the reason, never an estimate. The logic lives in
      `e2e_value.py` (see its docstring for the shapes); it never starts a gate.

stdlib + the `git`/`gh`-free `git_run` helper + (best-effort) the target
repo's own `.venv` pytest for the true node count.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
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
import e2e_value  # noqa: E402
from ux_surface import fenced_mask, parse_ux_surface_block  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

DEFAULT_TEST_DIRS = ["tests/e2e"]
DEFAULT_TARGET = 15

_CI_HEADING = re.compile(r"^##\s+CI expectations\b")
_E2E_SURFACE_LINE_RE = re.compile(r"e2e surface", re.IGNORECASE)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_TEST_DEF_RE = re.compile(r"^\s*def (test_\w+)")


# ---- pure helpers (unit-tested without git/pytest) ------------------------

# `fenced_mask` itself now lives in `ux_surface` (this module already imports
# from it) so `deploy_coverage` and this module share one fence-tracking
# implementation instead of re-deriving the CommonMark rule (fleet-config#817).


def find_ci_expectations_block(claude_md: str) -> Optional[str]:
    """Extract the `## CI expectations` section text, or None if absent.

    Stops at the next `## ` heading, same top-anchored-section convention as
    `ux_surface.py`'s block parser.

    Headings inside a fenced code block are ignored (fleet-config#602).
    project-scaffolding documents the `## CI expectations` template *verbatim
    inside a fence*, and matching that example made the audit resolve
    `test_dirs` to the template's bracketed placeholder text and report a
    confident 0 files for a repo that has five. The same mask guards the
    section-end scan, so a real block quoting a `## ` line in an example is no
    longer truncated at it.
    """
    lines = claude_md.splitlines()
    fenced = fenced_mask(lines)
    start = None
    for i, line in enumerate(lines):
        if not fenced[i] and _CI_HEADING.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return None
    out: List[str] = []
    for i in range(start, len(lines)):
        if not fenced[i] and lines[i].startswith("## "):
            break
        out.append(lines[i])
    return "\n".join(out)


def extract_backtick_paths(line: str) -> List[str]:
    """All backtick-quoted spans on a line, in order."""
    return _BACKTICK_RE.findall(line)


def filter_test_like_paths(paths: List[str]) -> List[str]:
    """Keep only paths that look like a test location (a `test` path segment).

    A CI-expectations e2e-surface sentence typically names source dirs too
    (`app/webapp/`, `src/session_host*.py`) alongside the actual test dir
    (`tests/e2e/`) — this audit only cares about the latter.

    A span must look like a path — a `/` or `\\` separator, or a `.py` file —
    before its segments are checked, so a backticked command word such as
    `pytest` in the same sentence is never read as a test dir
    (fleet-config#906).
    """
    out = []
    for p in paths:
        if not ("/" in p or "\\" in p or p.endswith(".py")):
            continue
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


def _dotted_name(node: ast.AST) -> str:
    """`pytest.mark.parametrize` from the attribute chain, or "" if not a name."""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _argnames_text(node: ast.AST) -> str:
    """`("width", "theme")` / `"width,theme"` -> a single canonical string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ",".join(p.strip() for p in node.value.split(","))
    if isinstance(node, (ast.Tuple, ast.List)):
        names = [e.value for e in node.elts
                 if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if names:
            return ",".join(names)
    return "?"


def _argvalues_source(node: ast.AST) -> str:
    """A stable identity for the parametrize *value source*.

    A shared module-level `MATRIX` is the shape this detector is named for, but
    two tests pasting the identical inline list are the same redundancy — so a
    literal collection collapses to a hash of its AST and collides just the
    same. Anything unrecognized returns "" and is skipped rather than guessed.
    """
    dotted = _dotted_name(node)
    if dotted:
        return f"name:{dotted}"
    if isinstance(node, ast.Call):
        fn = _dotted_name(node.func)
        return f"call:{fn}" if fn else ""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        digest = hashlib.sha1(ast.dump(node).encode("utf-8")).hexdigest()[:8]
        return f"literal:{digest}"
    return ""


def parametrize_signatures(source: str) -> Dict[str, str]:
    """`{test_name: "<argnames>@<value-source>"}` for parametrized tests.

    Only tests whose `@pytest.mark.parametrize` value source is identifiable
    (a named collection, a call, or a literal) appear; an unparseable file or
    an unrecognized decorator shape yields nothing rather than a guess.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    out: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            name = _dotted_name(dec.func)
            if not (name.endswith("mark.parametrize") or name == "parametrize"):
                continue
            if len(dec.args) < 2:
                continue
            src = _argvalues_source(dec.args[1])
            if src:
                out[node.name] = f"{_argnames_text(dec.args[0])}@{src}"
            break
    return out


def matrix_candidates(files: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Tests in one file sweeping the same parametrize matrix, >=2 members.

    The blind spot `cluster_candidates` cannot see (fleet-config#602): it
    groups on *normalized names*, so four differently-named tests each
    decorated with the same 8-leg `MATRIX` never collide, and the audit
    reported a confident "0 clusters" over 32 collected nodes. That verdict
    was acted on — project-scaffolding#209 closed calling the sweep deliberate
    breakpoint coverage, and was reopened after PR #219 collapsed those same
    tests 32 nodes -> 8 with no coverage loss.

    Like every other signal here this is a *candidate*, not a verdict — a
    shared matrix across genuinely distinct assertions is legitimate. The
    point is that the LLM layer gets to see it at all.
    """
    out: List[Dict[str, object]] = []
    for f in files:
        sigs = f.get("parametrized") or {}
        if not isinstance(sigs, dict):
            continue
        by_sig: Dict[str, List[str]] = {}
        for name, sig in sigs.items():
            by_sig.setdefault(sig, []).append(name)
        for sig, members in sorted(by_sig.items()):
            if len(members) < 2:
                continue
            argnames, _, source = sig.partition("@")
            out.append({
                "file": f.get("file"),
                "signature": sig,
                "argnames": argnames,
                "source": source,
                "members": sorted(members),
            })
    return out


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


def split_resolved_dirs(repo_root: Path, test_dirs: List[str]) -> tuple:
    """`(existing, missing)` split of `test_dirs` against what's on disk.

    The measurement behind `test_dirs_resolved` (fleet-config#602). A scan
    whose resolved dirs match nothing real found 0 files because it had
    nowhere to look — a different fact from "this repo has no e2e tests", and
    the fleet's rule is that an unestablished fact reports as its own state
    rather than folding into the passing one.
    """
    existing = [d for d in test_dirs if (repo_root / d).is_dir()]
    missing = [d for d in test_dirs if d not in existing]
    return existing, missing


_COLLECTED_TOTAL_RE = re.compile(r"^(\d+) tests? collected", re.MULTILINE)
_COLLECTED_PER_FILE_RE = re.compile(r"^\S+\.py: (\d+)$", re.MULTILINE)


def parse_collected_count(stdout: str) -> Optional[int]:
    """Node count from `pytest --collect-only -q` output, or None.

    The `N tests collected` total wins when present. A repo whose `addopts`
    already carries `-q` runs at `-qq`, where pytest prints only per-file
    `tests/e2e/test_x.py: N` lines and no total — those are summed instead
    (fleet-config#900: task-os and whatsapp-radar read "not measured"). Output
    with neither shape is None, never a guessed zero.
    """
    m = _COLLECTED_TOTAL_RE.search(stdout)
    if m:
        return int(m.group(1))
    per_file = _COLLECTED_PER_FILE_RE.findall(stdout)
    if per_file:
        return sum(int(n) for n in per_file)
    return None


def target_ratio(total_tests: int, target: int) -> float:
    if target <= 0:
        return 0.0
    return round(total_tests / target, 2)


def budget_limit(fleet_toml_text: Optional[str], default: int = DEFAULT_TARGET) -> tuple:
    """`(limit, source, note)` for the suite budget.

    `.fleet.toml` `[e2e] test_budget` wins when it is a positive integer —
    a repo sets it at its current size and lowers it as it trims. Absent,
    unparsable, or not a positive int (a bool, a string, 0) falls back to the
    scaffold target, with a note saying why, so a typo can never silently
    raise the bar.
    """
    if not fleet_toml_text:
        return default, "scaffold-target", "no .fleet.toml"
    import tomllib
    try:
        data = tomllib.loads(fleet_toml_text)
    except tomllib.TOMLDecodeError:
        return default, "scaffold-target", ".fleet.toml could not be parsed"
    e2e = data.get("e2e")
    if e2e is not None and not isinstance(e2e, dict):
        return default, "scaffold-target", "[e2e] is not a table"
    raw = e2e.get("test_budget") if e2e is not None else None
    if raw is None:
        return default, "scaffold-target", "no [e2e] test_budget declared"
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return default, "scaffold-target", f"invalid [e2e] test_budget {raw!r} ignored"
    return raw, "declared", "[e2e] test_budget"


def budget_verdict(dirs_resolved: bool, files: int, node_count: Optional[int],
                   raw_tests: int, limit: int) -> tuple:
    """`(verdict, count, count_kind)` for the suite budget.

    Nodes are the measure (parametrize expansion and browser projections are
    what the gate pays for). When nodes cannot be measured, the raw function
    count is a lower bound: it can prove `over`, never `within`. Test dirs
    that resolve to nothing on disk are `unmeasured` — the scan had nowhere to
    look, which is not the same fact as "no suite" (fleet-config#602).
    """
    if not dirs_resolved:
        return "unmeasured", 0, "unresolved-test-dirs"
    if files == 0:
        return "no-suite", 0, "files"
    if node_count is not None:
        return ("over" if node_count > limit else "within"), node_count, "nodes"
    if raw_tests > limit:
        return "over", raw_tests, "functions-lower-bound"
    return "unmeasured", raw_tests, "functions-lower-bound"


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
    return {
        "file": rel_path,
        "lines": len(lines),
        "tests": names,
        "parametrized": parametrize_signatures(text),
    }


def collect_pytest_node_count(repo_root: Path, test_dirs: List[str]) -> Optional[int]:
    """Best-effort true pytest node count (parametrize-expanded), or None.

    Requires the target repo's own `.venv` — never assumes a shared/global
    pytest. Any failure (no venv, pytest not installed, collection error)
    reports None rather than crashing; the caller treats that as "not
    measured", matching the fleet's fail-open convention for optional
    measurements.

    Every `None` exit writes a one-line reason to stderr first. A silent
    `None` is indistinguishable from a genuinely tiny suite once `scan`
    substitutes the raw function count into `ratio`, so a repo whose pytest
    collection is *broken* produced the same headline number as a healthy one
    (fleet-config#679). `index_lock.git_processes_running` is the in-repo
    model for this shape.
    """
    python_exe = repo_root / ".venv" / "Scripts" / "python.exe"
    if not python_exe.is_file():
        sys.stderr.write(
            f"e2e_test_audit: node count not measured — no venv interpreter at {python_exe}\n"
        )
        return None
    existing_dirs = [d for d in test_dirs if (repo_root / d).is_dir()]
    if not existing_dirs:
        sys.stderr.write(
            f"e2e_test_audit: node count not measured — none of the declared test dirs "
            f"{test_dirs or '[]'} exist under {repo_root}\n"
        )
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
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(
            f"e2e_test_audit: node count not measured — pytest --collect-only failed in "
            f"{repo_root} ({type(exc).__name__}: {exc})\n"
        )
        return None
    count = parse_collected_count(res.stdout or "")
    if count is None:
        tail = (res.stdout or res.stderr or "").strip().replace("\n", " ")[-200:]
        sys.stderr.write(
            f"e2e_test_audit: node count not measured — neither an 'N tests collected' line "
            f"nor per-file 'path.py: N' lines in pytest output for {repo_root} "
            f"(exit {res.returncode}): {tail!r}\n"
        )
    return count


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
    existing_dirs, missing_dirs = split_resolved_dirs(repo_root, test_dirs)

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
        "test_dirs_resolved": bool(existing_dirs),
        "test_dirs_missing": missing_dirs,
        "files": files,
        "totals": {
            "files": len(files),
            "raw_tests": len(all_tests),
            "node_count": node_count,
        },
        "target": target,
        "ratio": target_ratio(node_count if node_count is not None else len(all_tests), target),
        "clusters": cluster_candidates(all_tests),
        "matrix_clusters": matrix_candidates(files),
        "size_outliers": size_outliers(files),
        "key_views_declared": key_views,
        "coverage_gaps": coverage_gaps(key_views, all_test_text) if key_views else [],
    }


def cmd_scan(repo_root: Path, target: int) -> int:
    print(json.dumps(scan(repo_root, target)))
    return 0


def _test_dirs(repo_root: Path) -> List[str]:
    claude_md = repo_root / "CLAUDE.md"
    return resolve_test_dirs(claude_md.read_text(encoding="utf-8", errors="replace") if claude_md.is_file() else None)


def cmd_budget(repo_root: Path) -> int:
    claude_md_path = repo_root / "CLAUDE.md"
    claude_md_text = (
        claude_md_path.read_text(encoding="utf-8", errors="replace")
        if claude_md_path.is_file() else None
    )
    test_dirs = resolve_test_dirs(claude_md_text)
    files = [rel for d in test_dirs for rel in _list_files(repo_root, d)]
    raw_tests = sum(len(parse_test_file(repo_root, rel)["tests"]) for rel in files)  # type: ignore[arg-type]
    node_count = collect_pytest_node_count(repo_root, test_dirs) if files else None

    fleet_toml = repo_root / ".fleet.toml"
    toml_text = fleet_toml.read_text(encoding="utf-8", errors="replace") if fleet_toml.is_file() else None
    limit, source, note = budget_limit(toml_text)
    existing_dirs, _missing = split_resolved_dirs(repo_root, test_dirs)
    verdict, count, kind = budget_verdict(bool(existing_dirs), len(files), node_count, raw_tests, limit)

    print(f"E2E_BUDGET={verdict}")
    print(f"E2E_BUDGET_COUNT={count}")
    print(f"E2E_BUDGET_COUNT_KIND={kind}")
    print(f"E2E_BUDGET_LIMIT={limit}")
    print(f"E2E_BUDGET_SOURCE={source}")
    print(f"E2E_BUDGET_REASON={kind} {count} vs limit {limit} ({note}); test dirs {','.join(test_dirs)}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic e2e test-suite inventory for /e2e-audit.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="inventory a repo's e2e test suite")
    p_scan.add_argument("repo", type=Path)
    p_scan.add_argument("--target", type=int, default=DEFAULT_TARGET)

    p_budget = sub.add_parser("budget", help="compare the suite's size to its declared budget")
    p_budget.add_argument("repo", type=Path)

    p_timing = sub.add_parser("timing", help="where the last completed gate run's time went")
    p_timing.add_argument("repo", type=Path)
    p_timing.add_argument("--log", type=Path, default=None)

    p_fail = sub.add_parser("failures", help="failure history from the gate logs and GitHub text")
    p_fail.add_argument("repo", type=Path)
    p_fail.add_argument("--log", type=Path, default=None)
    p_fail.add_argument("--prs", type=int, default=60)
    p_fail.add_argument("--no-gh", action="store_true")

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    if args.cmd == "budget":
        return cmd_budget(repo)
    if args.cmd == "timing":
        print(json.dumps(e2e_value.timing(repo, _test_dirs(repo), args.log)))
        return 0
    if args.cmd == "failures":
        print(json.dumps(e2e_value.failures(repo, args.log, args.prs, use_gh=not args.no_gh)))
        return 0
    return cmd_scan(repo, args.target)


if __name__ == "__main__":
    raise SystemExit(main())
