"""Deterministic entry-point inventory and dead-code verdict for one repo (fleet-config#961).

A research spike: "is this code used?" answered with no LLM anywhere. Static
analysis decides what is dead *inside* an entry point; runtime evidence decides
which entry points are *alive*.

- `collect <repo>` (impure, report-only) snapshots the runtime evidence that
  already exists: app-launcher jobs (`GET /api/jobs`, loopback, so no token) and
  Windows scheduled tasks whose action points into the repo, each with a
  last-run time converted to UTC. A source that can't be read is recorded as an
  error, and every entry point it would have covered becomes `unknown`.
  It also summarises the interpreter-start beacon's ledger when the repo has one
  (`start_beacon.py`, fleet-config#1114): the last hit per script and how much
  of the window the beacon covered.
- `verdict <repo>` is a pure function of the git tree (`git ls-files`), that
  snapshot, `--as-of` and `--window-days`: the same inputs give byte-identical
  output. `run <repo>` is `collect` then `verdict`.

Entry points: Python files with a `__main__` guard, `.bat`/`.cmd`/`.ps1`/`.sh`
launchers, the scripts those launchers run (`x.py`, `-m pkg.mod`), job and task
targets. Modules are reached through a stdlib-`ast` import graph from every
entry point. Tests are never roots, so a module only tests import is
`unreachable (test-only)`.

States, never folded into one another:
- `live`: an entry point (or a module one reaches) run within the window;
- `cold`: covered by a job/task, but not run within the window, or a Python entry
  with no beacon hit while the beacon covered the whole window (`beacon-no-hit`).
  It is a candidate, never a deletion;
- `unknown`: no evidence source covers it, or the beacon has not covered the
  whole window yet (`beacon-young`) or stopped covering it (`beacon-inactive`), or it uses dynamic dispatch
  (`importlib`, `__import__`, `exec`/`eval`, `getattr` with a computed name), or its
  name is mentioned as text elsewhere (name matching is conservative: a collision
  makes code look used). Reported per reason, and never counted as dead;
- `unreachable`: a module no entry point imports.

Keep entries (`--keep FILE`, a JSON list of `{path, reason, expires}`) mark
seasonal code; an unexpired keep takes a path off the candidate list.

Output (machine-local, never committed; paths, kinds and timestamps only):
`<hooks state>/dead_code/<repo>/{evidence,verdict}.json`, plus a table on stdout.
The tool never writes, deletes or moves anything in the target repo.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import ssl
import subprocess
import sys
import urllib.request
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from git_run import run_git  # noqa: E402
from hooks_state import state_dir  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
import start_beacon  # noqa: E402

SCHEMA = 1
WINDOW_DAYS = 90
JOBS_URL = "https://127.0.0.1:8445/api/jobs"
POWERSHELL = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
LAUNCHER_EXTS = (".bat", ".cmd", ".ps1", ".sh")
DOC_EXTS = (".md", ".txt", ".rst")
# Where a module's name can be mentioned as text (config, launchers, markup); binaries are never read.
TEXT_EXTS = LAUNCHER_EXTS + (".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".html", ".js", ".xml")
_TASKS_PS = (
    "Get-ScheduledTask | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; "
    "$l = if ($i -and $i.LastRunTime -and $i.LastRunTime.Year -gt 2000) "
    "{ $i.LastRunTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ') } else { $null }; "
    "[pscustomobject]@{ name = $_.TaskPath + $_.TaskName; last_run_utc = $l; "
    "actions = @($_.Actions | ForEach-Object { \"$($_.Execute) $($_.Arguments)\" }) } } | ConvertTo-Json -Depth 3 -Compress"
)
_PY_TOKEN = re.compile(r"""["']?([^\s"'=|&<>]*?\.py)\b""", re.I)
_MOD_TOKEN = re.compile(r"(?:^|\s)-m\s+([A-Za-z_][\w.]*)")
_LAUNCHER_TOKEN = re.compile(r"""["']?([^\s"'=|&<>]*?\.(?:bat|cmd|ps1|sh))\b""", re.I)
_COMMENT_LINE = re.compile(r"^\s*(?:@?rem\b|::|#|@?echo\b|write-host\b)", re.I)
# Any variable prefix (`%~dp0`, `%SCRIPT_DIR%`, `$PSScriptRoot`, `${DIR}`, `$env:X`, `$(dirname "$0")`):
# stripped, then the rest resolves against the launcher's own folder, then the repo root.
_DIR_VARS = re.compile(r'%~dp0|%[A-Za-z_][\w:~,-]*%|\$env:\w+|\$\{[^}]*\}|\$\([^)]*\)|\$\w+', re.I)


# ---- evidence (collect) -------------------------------------------------------

def _repo_rel(repo: Path, raw: str) -> Optional[str]:
    """`raw` (any path spelling) relative to `repo`, or None when outside it."""
    try:
        rel = Path(raw.strip().strip('"')).resolve().relative_to(repo.resolve())
    except (ValueError, OSError):
        return None
    return rel.as_posix()


def _local_to_utc(stamp: Optional[str]) -> Optional[str]:
    """The launcher's naive local `started_at` in UTC (three-clocks rule)."""
    if not stamp:
        return None
    moment = datetime.fromisoformat(stamp)
    moment = moment.astimezone(timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_jobs(repo: Path, url: str = JOBS_URL) -> List[Dict[str, Any]]:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    with urllib.request.urlopen(url, timeout=60, context=ctx) as resp:
        jobs = json.loads(resp.read().decode("utf-8")).get("jobs") or []
    out = []
    for job in jobs:
        target = _repo_rel(repo, str(job.get("script_path") or ""))
        if target:
            last = job.get("last_run") or {}
            out.append({"id": str(job.get("id")), "target": target, "paused": bool(job.get("paused")),
                        "last_run_utc": _local_to_utc(last.get("started_at"))})
    return sorted(out, key=lambda j: j["id"])


def collect_tasks(repo: Path) -> List[Dict[str, Any]]:
    res = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", _TASKS_PS],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=300, creationflags=NO_WINDOW)
    if res.returncode != 0:
        raise RuntimeError(f"powershell exit {res.returncode}: {res.stderr.strip()[:200]}")
    rows = json.loads(res.stdout or "[]")
    rows = rows if isinstance(rows, list) else [rows]
    root = str(repo.resolve()).lower().replace("/", "\\")
    out = []
    for row in rows:
        for action in row.get("actions") or []:
            for token in re.findall(r'"([^"]+)"|(\S+)', action):
                raw = token[0] or token[1]
                if raw.lower().replace("/", "\\").startswith(root):
                    target = _repo_rel(repo, raw)
                    if target:
                        out.append({"name": row.get("name"), "target": target, "last_run_utc": row.get("last_run_utc")})
    return sorted(out, key=lambda t: (t["name"], t["target"]))


def collect_beacon(repo: Path, venv: Path, ledger: Optional[Path] = None) -> Dict[str, Any]:
    """The interpreter-start ledger (fleet-config#1114) summarised: coverage and last hit per target.

    `state`: `absent` (no ledger, no `.pth`: the repo has no beacon), `active`
    (installed and the `.pth` still present), `uninstalled` (coverage ended at
    the header), `removed` (the `.pth` is gone with no uninstall header, so
    coverage ended at an unknown time), `no-header` (records with no install
    header). Only `active` and `uninstalled` can turn a missing hit into `cold`.
    """
    ledger = ledger or start_beacon.ledger_path(repo)
    pth = start_beacon.pth_path(venv)
    present = bool(pth and pth.exists())
    out: Dict[str, Any] = {"pth_present": present, "installed_utc": None, "uninstalled_utc": None,
                           "hits": {}, "outside": 0, "no_script": 0, "malformed": 0}
    if not ledger.exists():
        out["state"] = "active" if present else "absent"
        return out
    headers, raw_hits, out["malformed"] = start_beacon.read_ledger(ledger)
    installs = [i for i, (kind, _) in enumerate(headers) if kind == "installed"]
    if installs:
        out["installed_utc"] = headers[installs[-1]][1]
        after = [utc for kind, utc in headers[installs[-1] + 1:] if kind == "uninstalled"]
        out["uninstalled_utc"] = after[0] if after else None
        out["state"] = "uninstalled" if after else ("active" if present else "removed")
    else:
        out["state"] = "no-header"
    hits: Dict[str, str] = {}
    for raw, utc in raw_hits.items():
        if raw in ("", "-c"):
            out["no_script"] += 1
            continue
        target = raw if raw.startswith("-m ") else _repo_rel(repo, raw)
        if target is None:
            out["outside"] += 1
        elif utc > hits.get(target, ""):
            hits[target] = utc
    out["hits"] = dict(sorted(hits.items()))
    return out


def collect(repo: Path, jobs_url: str = JOBS_URL, tasks: bool = True, venv: Optional[Path] = None,
            ledger: Optional[Path] = None) -> Dict[str, Any]:
    evidence: Dict[str, Any] = {"schema": SCHEMA, "repo": repo.name,
                                "collected_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    for key, fn in (("jobs", lambda: collect_jobs(repo, jobs_url)), ("tasks", (lambda: collect_tasks(repo)) if tasks else list),
                    ("beacon", lambda: collect_beacon(repo, venv or repo / ".venv", ledger))):
        try:
            evidence[key], evidence[f"{key}_error"] = fn(), None
        except Exception as exc:  # noqa: BLE001 -- an unreadable source is recorded, and its entries go unknown
            evidence[key], evidence[f"{key}_error"] = [], f"{type(exc).__name__}: {str(exc)[:200]}"
    return evidence


# ---- static analysis (verdict) ---------------------------------------------------

def tracked_files(repo: Path) -> List[str]:
    res = run_git(["-C", str(repo), "ls-files", "-z"], check=True)
    return sorted(p for p in res.stdout.split("\0") if p)


def is_test(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(p in ("tests", "test") for p in parts[:-1]) or parts[-1].startswith("test_") or parts[-1] == "conftest.py"


class PyFacts:
    def __init__(self, path: str, source: str) -> None:
        self.path, self.imports, self.dynamic, self.main_guard, self.parse_error = path, [], set(), False, None
        self.lines = source.count("\n") + (0 if source.endswith("\n") or not source else 1)
        try:
            with warnings.catch_warnings():  # the target's own invalid-escape warnings are not ours to print
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(source)
        except (SyntaxError, ValueError) as exc:
            self.parse_error = type(exc).__name__
            return
        for node in tree.body:
            if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
                self.main_guard = True
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.imports += [(alias.name, 0, ()) for alias in node.names]
                if any(alias.name == "importlib" for alias in node.names):
                    self.dynamic.add("importlib")
            elif isinstance(node, ast.ImportFrom):
                self.imports.append((node.module or "", node.level, tuple(a.name for a in node.names)))
                if node.module == "importlib":
                    self.dynamic.add("importlib")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in ("__import__", "exec", "eval"):
                    self.dynamic.add(node.func.id)
                elif (node.func.id == "getattr" and len(node.args) >= 2
                      and not (isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str))):
                    self.dynamic.add("getattr")


def _resolve_module(dotted: str, base_dirs: Iterable[PurePosixPath], files: Set[str]) -> List[str]:
    parts = [p for p in dotted.split(".") if p]
    hits = []
    for base in base_dirs:
        stem = base.joinpath(*parts) if parts else base
        for cand in (f"{stem}.py", f"{stem}/__init__.py"):
            if cand in files:
                hits.append(cand)
        if hits:
            break
    return hits


def import_edges(facts: PyFacts, files: Set[str]) -> List[str]:
    """Tracked modules `facts` imports: its own folder first, then each parent, then the root."""
    here = PurePosixPath(facts.path).parent
    ancestors = [here, *here.parents]
    targets: Set[str] = set()
    for module, level, names in facts.imports:
        bases = [ancestors[level - 1]] if level and level - 1 < len(ancestors) else ancestors
        found = _resolve_module(module, bases, files)
        targets.update(found)
        for name in names:  # `from pkg import sub` may name a submodule
            targets.update(_resolve_module(f"{module}.{name}" if module else name, bases, files))
    return sorted(t for t in targets if t != facts.path)


def launcher_targets(path: str, text: str, files: Set[str]) -> List[str]:
    """Tracked scripts and launchers a launcher runs (comments and echo lines skipped)."""
    here = PurePosixPath(path).parent
    out: Set[str] = set()

    def resolve(raw: str) -> Optional[str]:
        raw = _DIR_VARS.sub("", raw).replace("\\", "/").lstrip("/")
        for base in (here, PurePosixPath(".")):
            cand = PurePosixPath(*[p for p in (base / raw).parts if p not in (".",)])
            norm: List[str] = []
            for part in cand.parts:
                if part == "..":
                    if norm:
                        norm.pop()
                else:
                    norm.append(part)
            joined = "/".join(norm)
            if joined in files:
                return joined
        sibling = str(here / PurePosixPath(raw).name) if str(here) != "." else PurePosixPath(raw).name
        return sibling if sibling in files else None

    for line in text.splitlines():
        if _COMMENT_LINE.match(line):
            continue
        for raw in _PY_TOKEN.findall(line) + _LAUNCHER_TOKEN.findall(line):
            hit = resolve(raw)
            if hit and hit != path:
                out.add(hit)
        for module in _MOD_TOKEN.findall(line):
            hit = resolve(module.replace(".", "/") + ".py") or resolve(module.replace(".", "/") + "/__main__.py")
            if hit:
                out.add(hit)
    return sorted(out)


def _read(repo: Path, rel: str) -> str:
    try:
        return (repo / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def verdict(repo: Path, evidence: Dict[str, Any], as_of: date, window_days: int = WINDOW_DAYS,
            keeps: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    files = tracked_files(repo)
    fileset = set(files)
    py = {p: PyFacts(p, _read(repo, p)) for p in files if p.endswith(".py")}
    launchers = [p for p in files if p.lower().endswith(LAUNCHER_EXTS)]
    texts = {p: _read(repo, p) for p in files if p.lower().endswith(TEXT_EXTS)}
    docs = {p: _read(repo, p) for p in files if p.lower().endswith(DOC_EXTS)}

    launched_by: Dict[str, Set[str]] = {}
    for launcher in launchers:
        for target in launcher_targets(launcher, texts.get(launcher, ""), fileset):
            launched_by.setdefault(target, set()).add(f"launcher:{launcher}")
    covered: Dict[str, List[Tuple[str, Optional[str]]]] = {}  # direct job/task evidence per target
    for job in evidence.get("jobs") or []:
        covered.setdefault(job["target"], []).append((f"job:{job['id']}" + (" (paused)" if job.get("paused") else ""), job.get("last_run_utc")))
    for task in evidence.get("tasks") or []:
        covered.setdefault(task["target"], []).append((f"task:{task['name']}", task.get("last_run_utc")))
    for target, rows in covered.items():
        launched_by.setdefault(target, set()).update(src for src, _ in rows)
    for doc, text in docs.items():
        for path in files:
            if not is_test(path) and (path in text or (path.endswith(".py") and PurePosixPath(path).name in text)):
                launched_by.setdefault(path, set()).add(f"doc:{doc}")

    # Interpreter-start hits (fleet-config#1114): a `-m pkg.mod` hit resolves from the repo root.
    beacon = evidence.get("beacon") if isinstance(evidence.get("beacon"), dict) else {}
    beacon_state = "error" if evidence.get("beacon_error") else beacon.get("state", "absent")
    beacon_hits: Dict[str, str] = {}
    for raw, utc in (beacon.get("hits") or {}).items():
        if raw.startswith("-m "):
            stem = raw[3:].strip().replace(".", "/")
            raw = next((c for c in (f"{stem}.py", f"{stem}/__main__.py") if c in fileset), "")
        if raw in fileset and not is_test(raw) and utc > beacon_hits.get(raw, ""):
            beacon_hits[raw] = utc
    for path in beacon_hits:
        launched_by.setdefault(path, set()).add("beacon")

    entries = sorted({p for p, f in py.items() if f.main_guard and not is_test(p)} | set(launchers)
                     | {t for t, srcs in launched_by.items() if any(not s.startswith("doc:") for s in srcs)})
    # Last seen: direct evidence, then propagated down launcher chains (a job's .bat runs its .py).
    last_seen: Dict[str, Tuple[Optional[str], str]] = {}
    for target, rows in covered.items():
        runs = sorted((r for _, r in rows if r), reverse=True)
        last_seen[target] = (runs[0] if runs else None, rows[0][0])
    changed = True
    while changed:
        changed = False
        for target, srcs in sorted(launched_by.items()):
            for src in sorted(srcs):
                if src.startswith("launcher:") and src[9:] in last_seen:
                    seen, source = last_seen[src[9:]]
                    best = last_seen.get(target)
                    if best is None or (seen or "") > (best[0] or ""):
                        last_seen[target] = (seen, f"{source} via {src[9:]}")
                        changed = True
    for path, utc in beacon_hits.items():
        if path not in last_seen or utc > (last_seen[path][0] or ""):
            last_seen[path] = (utc, "beacon")

    as_of_utc = datetime.combine(as_of, datetime.min.time(), timezone.utc)
    cutoff = (as_of_utc - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    evidence_down = [k for k in ("jobs", "tasks") if evidence.get(f"{k}_error")]
    # A missing hit means "not run" only when the beacon covered the whole window:
    # installed by the cutoff and still in place (or uninstalled no earlier than as-of).
    coverage_end = beacon.get("uninstalled_utc") or (evidence.get("collected_utc") if beacon_state == "active" else None)
    beacon_covers = (beacon_state in ("active", "uninstalled") and bool(beacon.get("installed_utc"))
                     and beacon["installed_utc"] <= cutoff and (coverage_end or "") >= as_of_utc.strftime("%Y-%m-%dT%H:%M:%SZ"))
    importers: Dict[str, Set[str]] = {}
    for path, facts in py.items():
        for target in import_edges(facts, fileset):
            importers.setdefault(target, set()).add(path)

    rows: Dict[str, Dict[str, Any]] = {}
    for path in entries:
        facts = py.get(path)
        srcs = sorted(launched_by.get(path, ()))
        row: Dict[str, Any] = {"path": path, "kind": "launcher" if path in launchers else "entry",
                               "launched_by": srcs, "lines": facts.lines if facts else _read(repo, path).count("\n")}
        seen = last_seen.get(path)
        if facts and (facts.dynamic or facts.parse_error):
            row.update(state="unknown", reason="dynamic-dispatch" if facts.dynamic else "parse-error",
                       dynamic=sorted(facts.dynamic))
        elif seen is not None:
            row.update(last_seen=seen[0], last_seen_source=seen[1])
            if seen[0] and seen[0] >= cutoff:
                row.update(state="live")
            elif seen[1] == "beacon" and not beacon_covers:  # an old hit says nothing about an uncovered window
                row.update(state="unknown", reason="beacon-young" if beacon_state == "active" else "beacon-inactive")
            else:
                row.update(state="cold")
        elif evidence_down:
            row.update(state="unknown", reason="evidence-unavailable")
        elif beacon_state != "absent" and path.endswith(".py"):
            # The beacon sees Python starts only; launchers keep their reasons below.
            if beacon_covers:
                row.update(state="cold", reason="beacon-no-hit", last_seen=None, last_seen_source="beacon")
            else:
                row.update(state="unknown", reason="beacon-young" if beacon_state == "active" else "beacon-inactive")
        elif path in launchers or any(s.startswith("launcher:") for s in srcs):
            row.update(state="unknown", reason="manual-launcher")
        elif srcs:
            row.update(state="unknown", reason="doc-mention-only")
        elif importers.get(path):
            row.update(state="unknown", reason="imported-by-code")
        else:
            row.update(state="unknown", reason="no-launcher")
        rows[path] = row

    # Modules: reached through imports from any entry point (tests are not roots).
    rank = {"live": 3, "unknown": 2, "cold": 1}
    reach: Dict[str, str] = {}
    frontier = [(p, r["state"]) for p, r in rows.items() if p in py]
    while frontier:
        path, state = frontier.pop()
        for target in import_edges(py[path], fileset):
            if target in rows:
                continue
            if rank.get(state, 0) > rank.get(reach.get(target, ""), 0):
                reach[target] = state
                frontier.append((target, state))
    code_texts = {p: t for p, t in texts.items()} | {p: _read(repo, p) for p in py}
    for path, facts in py.items():
        if path in rows or is_test(path):
            continue
        row = {"path": path, "kind": "module", "lines": facts.lines}
        if path in reach:
            row.update(state=reach[path], reason="imported")
        elif facts.dynamic or facts.parse_error:
            row.update(state="unknown", reason="dynamic-dispatch" if facts.dynamic else "parse-error", dynamic=sorted(facts.dynamic))
        elif PurePosixPath(path).name == "__init__.py" and not facts.lines:
            continue
        else:
            stem = PurePosixPath(path).stem if PurePosixPath(path).name != "__init__.py" else PurePosixPath(path).parent.name
            by_tests = sorted(p for p in importers.get(path, ()) if is_test(p))
            mentioned = sorted(p for p, t in code_texts.items() if p != path and not is_test(p) and re.search(rf"\b{re.escape(stem)}\b", t))
            if by_tests and not [p for p in importers.get(path, ()) if not is_test(p)]:
                row.update(state="unreachable", reason="test-only", imported_by_tests=by_tests)
            elif mentioned:
                row.update(state="unknown", reason="name-mentioned", mentioned_in=mentioned[:5])
            else:
                row.update(state="unreachable", reason="no-importer")
        rows[path] = row

    active_keeps = {}
    unmatched_keeps = sorted(str(k.get("path")) for k in keeps or [] if k.get("path") not in rows)
    for keep in keeps or []:
        expired = keep.get("expires") and str(keep["expires"]) < as_of.isoformat()
        if keep.get("path") in rows:
            rows[keep["path"]]["keep"] = {"reason": keep.get("reason"), "expires": keep.get("expires"), "expired": bool(expired)}
            if not expired:
                active_keeps[keep["path"]] = keep
    candidates = [r for r in rows.values()
                  if (r["state"] == "unreachable" or (r["state"] == "unknown" and r.get("reason") == "no-launcher"))
                  and r["path"] not in active_keeps]
    counts: Dict[str, int] = {}
    reasons: Dict[str, int] = {}
    for r in rows.values():
        counts[r["state"]] = counts.get(r["state"], 0) + 1
        if r["state"] == "unknown":
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    return {"schema": SCHEMA, "repo": repo.name, "as_of": as_of.isoformat(), "window_days": window_days,
            "evidence": {**{k: evidence.get(k) for k in ("collected_utc", "jobs_error", "tasks_error", "beacon_error")},
                         "beacon": {"state": beacon_state, "covers_window": beacon_covers, "hits": len(beacon_hits),
                                    **{k: beacon.get(k) for k in ("installed_utc", "uninstalled_utc", "pth_present",
                                                                  "outside", "no_script", "malformed")}}},
            "counts": counts, "unknown_reasons": reasons,
            "candidates": [{"path": r["path"], "state": r["state"], "reason": r.get("reason"), "lines": r["lines"]}
                           for r in sorted(candidates, key=lambda r: r["path"])],
            "candidate_lines": sum(r["lines"] for r in candidates),
            "keeps_unmatched": unmatched_keeps,
            "note": "name matching is conservative: a name collision makes code look used, so this under-reports dead code",
            "files": [rows[p] for p in sorted(rows)]}


def table(doc: Dict[str, Any]) -> str:
    lines = [f"{doc['repo']} as of {doc['as_of']} (window {doc['window_days']} d): "
             + ", ".join(f"{k}={v}" for k, v in sorted(doc["counts"].items())),
             "unknown by reason: " + (", ".join(f"{k}={v}" for k, v in sorted(doc["unknown_reasons"].items())) or "none"),
             "beacon: " + ", ".join(f"{k}={v}" for k, v in sorted(doc["evidence"]["beacon"].items())),
             f"candidates: {len(doc['candidates'])} files, {doc['candidate_lines']} lines",
             "| state | reason | lines | path |", "|---|---|--:|---|"]
    lines += [f"| {c['state']} | {c['reason']} | {c['lines']} | {c['path']} |" for c in doc["candidates"]]
    return "\n".join(lines)


def _write_output(folder: Path, name: str, doc: Dict[str, Any]) -> Path:
    """The tool's only write: machine-local state, never the target repo."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="deterministic entry-point inventory + dead-code verdict (fleet-config#961)")
    ap.add_argument("command", choices=("collect", "verdict", "run"))
    ap.add_argument("repo")
    ap.add_argument("--evidence", default=None, help="evidence snapshot (default: the last `collect` for this repo)")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: the evidence's collection date)")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--keep", default=None, help="JSON list of {path, reason, expires} keep entries")
    ap.add_argument("--out-dir", default=None, help="output folder (default: <hooks state>/dead_code/<repo>)")
    ap.add_argument("--jobs-url", default=JOBS_URL)
    ap.add_argument("--venv", default=None, help="venv carrying the start beacon (default: <repo>/.venv)")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    out_dir = Path(args.out_dir) if args.out_dir else state_dir() / "dead_code" / repo.name
    if args.command in ("collect", "run"):
        evidence = collect(repo, args.jobs_url, venv=Path(args.venv).resolve() if args.venv else None)
        beacon = evidence["beacon"] if isinstance(evidence["beacon"], dict) else {}
        print(f"EVIDENCE={_write_output(out_dir, 'evidence.json', evidence)} jobs={len(evidence['jobs'])} "
              f"tasks={len(evidence['tasks'])} jobs_error={evidence['jobs_error']} tasks_error={evidence['tasks_error']} "
              f"beacon={beacon.get('state')} beacon_hits={len(beacon.get('hits') or {})} beacon_error={evidence['beacon_error']}")
        if args.command == "collect":
            return 0
    evidence_path = Path(args.evidence) if args.evidence else out_dir / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    as_of = date.fromisoformat(args.as_of or evidence["collected_utc"][:10])
    keeps = json.loads(Path(args.keep).read_text(encoding="utf-8")) if args.keep else None
    doc = verdict(repo, evidence, as_of, args.window_days, keeps)
    print(f"VERDICT={_write_output(out_dir, 'verdict.json', doc)}")
    print(table(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
