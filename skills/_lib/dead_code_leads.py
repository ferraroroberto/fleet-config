"""Deterministic dead-code leads for `/codebase-audit` bucket 2 (fleet-config#1527).

`leads <repo>` runs vulture over the repo's tracked Python and keeps only the
hits ruff cannot see: an unused function, class, method or property. They are
**leads, not findings** -- the auditing agent confirms each one with `refs`
before it enters the `stale` issue. `refs <repo> NAME...` is that caller
search: a whole-word count of each name over every tracked text file, so a
name used only from a `.bat`, `jobs.json` or a dispatch string still counts.

The floor is 60, not the 80 the issue first proposed: vulture scores an unused
function, class, method or property at a fixed 60 (imports 90, arguments and
unreachable code 100), so a higher floor reports none of the kinds this step
exists for. Output is filtered by kind instead.

A symbol referenced only from test files is tagged `test_only` -- found by a
second vulture pass over the non-test files, since the first pass counts a test
as a caller.

Every verdict is one of three states, and a failed run is never `ok`:
  ok              vulture ran and its output parsed; `hits` may be empty
  not-applicable  the repo tracks no Python
  not-confirmed   vulture missing/crashed/timed out, its output did not parse,
                  or the tracked-file list could not be read -- exit 3

`--ruff` adds ruff F401/F841 leads for a repo that has not enabled
`[static_ratchet]` (a ratchet repo already owns those through its baseline).

stdlib only apart from the vulture / ruff subprocesses.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_toml  # noqa: E402
from git_run import run_git  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

# Vulture scores unused functions, classes, methods and properties at exactly 60.
CONFIDENCE_FLOOR = 60
KEPT_KINDS = ("function", "class", "method", "property")
# Bound for one vulture run over a whole repo: ~2.5 s measured on 349 files, so
# 300 s only fails a hung process rather than a big repo.
VULTURE_TIMEOUT_S = 300
# A Windows command line tops out near 32k chars; past this the explicit file
# list would be truncated, so the run reports not-confirmed instead of guessing.
MAX_ARGV_CHARS = 30000

# Framework protocol overrides a caller outside the repo invokes by name.
PROTOCOL_NAMES = ("do_*", "handle_*", "visit_*", "log_message", "setUp*", "tearDown*", "on_*", "default")
FRAMEWORK_DECORATORS = (
    "@app.*", "@router.*", "@*.route", "@*.get", "@*.post", "@*.put", "@*.delete",
    "@*.websocket", "@*.on_event", "@pytest.fixture", "@pytest.hookimpl",
    "@pytest.mark.*", "@click.command", "@click.group",
)
# Every vulture line ends "(N% confidence)"; only `unused <kind> '<name>'` carries a symbol.
_VULTURE_LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+): (?P<what>.+) \((?P<conf>\d+)% confidence\)$")
_UNUSED = re.compile(r"^unused (?P<kind>[a-z ]+?) '(?P<name>[^']+)'$")
_TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|conftest\.py$)")


def is_test_path(rel: str) -> bool:
    return bool(_TEST_FILE.search(rel))


def tracked_files(repo: Path) -> Tuple[Optional[List[str]], str]:
    """`(posix relative paths, "")` of the repo's tracked files, or `(None, reason)`."""
    res = run_git(["-C", str(repo), "ls-files", "-z"])
    if res.returncode != 0:
        first = (res.stderr or "").strip().splitlines()
        return None, f"git ls-files failed (exit {res.returncode})" + (f": {first[0]}" if first else "")
    return [p for p in res.stdout.split("\0") if p], ""


def _spawn(cmd: Sequence[str], repo: Path) -> Tuple[Optional[subprocess.CompletedProcess], str]:
    try:
        return subprocess.run(
            list(cmd), cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=VULTURE_TIMEOUT_S, creationflags=NO_WINDOW,
        ), ""
    except subprocess.TimeoutExpired:
        return None, f"timed out after {VULTURE_TIMEOUT_S}s"
    except OSError as exc:
        return None, f"could not start ({type(exc).__name__}: {exc})"


def _first_line(text: str) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else ""


def run_vulture(repo: Path, files: Sequence[str], vulture_cmd: Sequence[str]) -> Tuple[Optional[List[Dict]], str]:
    """Parsed floor-60 hits of one vulture pass, or `(None, reason)` -- never `[]` for a failure."""
    cmd = [*vulture_cmd, *files, "--min-confidence", str(CONFIDENCE_FLOOR),
           "--ignore-decorators", ",".join(FRAMEWORK_DECORATORS)]
    if sum(len(c) + 1 for c in cmd) > MAX_ARGV_CHARS:
        return None, f"{len(files)} tracked Python files overflow the command line"
    proc, why = _spawn(cmd, repo)
    if proc is None:
        return None, why
    # vulture exits 0 clean, 3 when it reports dead code; anything else is a failure.
    if proc.returncode not in (0, 3):
        return None, f"vulture exited {proc.returncode}: {_first_line(proc.stderr) or _first_line(proc.stdout)}"
    hits: List[Dict] = []
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        m = _VULTURE_LINE.match(raw.strip())
        if not m:
            return None, f"unparseable vulture output: {raw.strip()[:120]}"
        sym = _UNUSED.match(m["what"])
        hits.append({"file": m["file"].replace("\\", "/"), "line": int(m["line"]),
                     "kind": sym["kind"] if sym else "other", "name": sym["name"] if sym else m["what"],
                     "confidence": int(m["conf"])})
    if proc.returncode == 3 and not hits:
        return None, "vulture reported dead code but no line parsed"
    return hits, ""


def _kept(hit: Dict) -> bool:
    name = hit["name"]
    if hit["kind"] not in KEPT_KINDS or is_test_path(hit["file"]):
        return False
    if name.startswith("__") and name.endswith("__"):
        return False
    return not any(fnmatch.fnmatchcase(name, pat) for pat in PROTOCOL_NAMES)


def _ratchet_enabled(repo: Path) -> bool:
    table = fleet_toml.table(fleet_toml.load(repo), "static_ratchet")
    return bool(table and table.get("enabled") is True)


def run_ruff(repo: Path, files: Sequence[str]) -> Dict:
    """Ruff F401/F841 leads for a repo outside the ratchet: `{state, reason, hits}`."""
    if _ratchet_enabled(repo):
        return {"state": "skipped", "reason": "[static_ratchet] enabled: the baseline owns F401/F841", "hits": []}
    cmd = [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--select", "F401,F841",
           "--output-format", "json", *files]
    if sum(len(c) + 1 for c in cmd) > MAX_ARGV_CHARS:
        return {"state": "not-confirmed", "reason": "tracked Python files overflow the command line", "hits": []}
    proc, why = _spawn(cmd, repo)
    if proc is None:
        return {"state": "not-confirmed", "reason": why, "hits": []}
    if proc.returncode not in (0, 1):
        reason = f"ruff exited {proc.returncode}: {_first_line(proc.stderr) or _first_line(proc.stdout)}"
        return {"state": "not-confirmed", "reason": reason, "hits": []}
    try:
        rows = json.loads(proc.stdout or "[]")
        root = repo.resolve()
        hits = [{"file": Path(r["filename"]).resolve().relative_to(root).as_posix(),
                 "line": r["location"]["row"], "kind": "import" if r["code"] == "F401" else "variable",
                 "name": r["message"], "code": r["code"]} for r in rows]
    except (ValueError, KeyError, TypeError):
        return {"state": "not-confirmed", "reason": "unparseable ruff output", "hits": []}
    return {"state": "ok", "reason": "", "hits": hits}


def leads(repo: Path, *, with_ruff: bool = False, vulture_cmd: Optional[Sequence[str]] = None) -> Dict:
    """The dead-code verdict for `repo`; see the module docstring for the states."""
    vulture_cmd = list(vulture_cmd) if vulture_cmd else [sys.executable, "-m", "vulture"]
    out: Dict = {"repo": str(repo), "floor": CONFIDENCE_FLOOR, "state": "ok", "reason": "",
                 "hits": [], "hit_count": 0, "ruff": None}
    tracked, why = tracked_files(repo)
    if tracked is None:
        return {**out, "state": "not-confirmed", "reason": why}
    py_files = [f for f in tracked if f.endswith(".py")]
    if not py_files:
        return {**out, "state": "not-applicable", "reason": "no tracked Python files"}
    everything, why = run_vulture(repo, py_files, vulture_cmd)
    if everything is None:
        return {**out, "state": "not-confirmed", "reason": why}
    # A test counts as a caller above; rerun without the tests to find symbols only a test keeps alive.
    prod_files = [f for f in py_files if not is_test_path(f)]
    prod, why = run_vulture(repo, prod_files, vulture_cmd) if prod_files else ([], "")
    if prod is None:
        return {**out, "state": "not-confirmed", "reason": f"test-only pass: {why}"}
    alive_somewhere: Set[Tuple[str, int, str]] = {(h["file"], h["line"], h["name"]) for h in everything}
    hits = [{**h, "test_only": False} for h in everything if _kept(h)]
    hits += [{**h, "test_only": True} for h in prod
             if _kept(h) and (h["file"], h["line"], h["name"]) not in alive_somewhere]
    hits.sort(key=lambda h: (h["file"], h["line"], h["name"]))
    out.update(hits=hits, hit_count=len(hits))
    if with_ruff:
        out["ruff"] = run_ruff(repo, py_files)
    return out


def refs(repo: Path, names: Sequence[str]) -> Dict:
    """Whole-word occurrences of each name over every tracked text file.

    The defining line (`def NAME` / `class NAME`) is excluded; a tracked file that
    cannot be read makes the whole answer `not-confirmed`, because a zero over
    files nobody read is not a zero.
    """
    tracked, why = tracked_files(repo)
    if tracked is None:
        return {"state": "not-confirmed", "reason": why, "names": {}}
    patterns = {n: re.compile(rf"(?<!\w){re.escape(n)}(?!\w)") for n in names}
    defs = {n: re.compile(rf"^\s*(async\s+)?(def|class)\s+{re.escape(n)}\b") for n in names}
    found: Dict[str, List[str]] = {n: [] for n in names}
    unreadable: List[str] = []
    for rel in tracked:
        try:
            data = (repo / rel).read_bytes()
        except OSError:
            unreadable.append(rel)
            continue
        if b"\0" in data:
            continue  # a binary file holds no callers worth counting
        for lineno, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            for n, pat in patterns.items():
                if pat.search(line) and not defs[n].match(line):
                    found[n].append(f"{rel}:{lineno}")
    result = {n: {"refs": len(locs), "files": len({loc.rsplit(':', 1)[0] for loc in locs}),
                  "test_only": bool(locs) and all(is_test_path(loc.rsplit(':', 1)[0]) for loc in locs),
                  "locations": locs[:10]} for n, locs in found.items()}
    if unreadable:
        return {"state": "not-confirmed", "reason": f"{len(unreadable)} tracked file(s) unreadable: {unreadable[:3]}",
                "names": result}
    return {"state": "ok", "reason": "", "names": result}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("leads", help="vulture dead function/class/method/property leads")
    lp.add_argument("repo")
    lp.add_argument("--ruff", action="store_true", help="also ruff F401/F841 leads (skipped under [static_ratchet])")
    rp = sub.add_parser("refs", help="caller search: whole-word occurrence count per name")
    rp.add_argument("repo")
    rp.add_argument("names", nargs="+")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    verdict = leads(repo, with_ruff=args.ruff) if args.cmd == "leads" else refs(repo, args.names)
    print(json.dumps(verdict, indent=2))
    ruff_failed = (verdict.get("ruff") or {}).get("state") == "not-confirmed"
    return 3 if verdict["state"] == "not-confirmed" or ruff_failed else 0


if __name__ == "__main__":
    sys.exit(main())
