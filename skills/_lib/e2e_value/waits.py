"""Where a suite pays wall time without doing anything (fleet-config#1134): sleeps and waits in the
test tree, slow nodes, app timers, slow fixtures, and runtime claims that drifted from the measurement.

Part of the `e2e_value` package; see its `__init__` for the reports and their shapes.
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .sources import test_of, _test_tree_files, _wall_s
from .fit import _test_texts

__all__ = [
    "DRIFT_TOLERANCE",
    "CLAIM_WINDOW",
    "SPAN_WORD_WINDOW",
    "LONG_TIMEOUT_MS",
    "POLL_CONSTANT_MIN_MS",
    "wait_sites",
    "SLOW_NODE_FACTOR",
    "SLOW_NODE_MIN_S",
    "APP_TIMER_MIN_MS",
    "slow_nodes",
    "app_timers",
    "SLOW_FIXTURE_TIMEOUT_S",
    "slow_fixtures",
    "rank_waits",
    "runtime_claims",
    "runtime_drift",
]


DRIFT_TOLERANCE = 0.25
_MINUTES_RE = re.compile(r"~?\s*(\d+(?:\.\d+)?)\s*(?:min\b|mins\b|minutes\b)", re.I)
_RUNTIME_CONTEXT_RE = re.compile(r"\b(gate|suite|e2e|verify|browser|pytest|runtime)\b", re.I)
CLAIM_WINDOW = 80
SPAN_WORD_WINDOW = 30
# The words just before a figure that make it a threshold, a history or a setting, not a measured runtime.
_NOT_A_CLAIM_RE = re.compile(
    r"\b(?:exceeds?|exceeding|over|above|beyond|more than|longer than|if|investigate|budget|limit|timeout|"
    r"after|every|cadence|default|previous|prior|was|were|old|before)\b[^.;]{0,12}$|>\s*\**\s*$", re.I)


# ---- waits: where a suite pays wall time without doing anything (fleet-config#1134) --------------------
# home-automation's biggest savings were waits, not merges: a real 15 s poll a test waited out (31 s of
# the suite, now `page.clock`) and nine copies of a fixed 750 ms page timer (16 s, now held responses).
# Merging 34 nodes saved about 10 s. A wait is found statically, so this works with no timing source.

LONG_TIMEOUT_MS = 10_000
POLL_CONSTANT_MIN_MS = 1_000
_SLEEP_MS_RE = re.compile(r"\bwait_for_timeout\(\s*([\d_]+(?:\.\d+)?)\s*\)")
_SLEEP_S_RE = re.compile(r"\b(?:time|asyncio)\.sleep\(\s*([\d_]*\.?\d+)\s*\)")
_TIMEOUT_KW_RE = re.compile(r"\btimeout\s*=\s*([\d_]+)\b")
_POLL_CONST_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*(?:_MS|_INTERVAL|POLL[A-Z0-9_]*))\s*=\s*([\d_]+)\s*(?:#.*)?$", re.M)


_NOOP_CALLBACK_RE = re.compile(r"^(?:function\s*\(\s*\)\s*\{\s*\}|\(\s*\)\s*=>\s*(?:\{\s*\}|undefined|null|void 0))$")


def _js_timer_args(text: str, fn: str = "setTimeout") -> List[Tuple[int, str, str]]:
    """`(offset, delay, callback)` for every `<fn>(cb, <delay>)`: the last argument of the call and the text before it."""
    out = []
    for m in re.finditer(rf"\b{fn}\(", text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        tail = re.search(r",\s*([\w.]+)\s*$", text[m.end():i - 1])
        if tail:
            out.append((m.start(), tail.group(1), text[m.end():m.end() + tail.start()].strip()))
    return out


def _js_timer_calls(text: str, fn: str = "setTimeout") -> List[Tuple[int, int, str]]:
    """`(offset, ms, callback)` for every `<fn>(cb, <ms>)` whose delay is a number literal."""
    return [(off, int(d.replace("_", "")), cb) for off, d, cb in _js_timer_args(text, fn) if re.fullmatch(r"\d[\d_]*", d)]


def _scopes(text: str) -> List[Tuple[int, int, str]]:
    """`(first_line, last_line, name)` of every function and module-level assignment, innermost last."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((n.lineno, n.end_lineno or n.lineno, n.name))
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            target = n.targets[0] if isinstance(n, ast.Assign) else n.target
            if isinstance(target, ast.Name):
                out.append((n.lineno, n.end_lineno or n.lineno, target.id))
    return sorted(out, key=lambda s: (s[0], -s[1]))


def _poll_loops(text: str) -> List[Tuple[int, int]]:
    """`(first_line, last_line)` of every `while` loop that can end on its own condition.

    A loop whose test is not a bare `True`, or whose body can `break`, `return`
    or `raise`, stops as soon as the condition it waits for holds. A bounded
    `for _ in range(40)` retry loop is a poll too when an `if` inside it
    `break`s or `return`s (app-launcher#1375: its `_wait_for_calls` was priced at
    the 40-iteration ceiling); a `for` loop over taps and an endless `while True`
    with no exit are not polls.
    """
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.While):
            endless = isinstance(n.test, ast.Constant) and n.test.value is True
            exits = any(isinstance(c, (ast.Break, ast.Return, ast.Raise)) for b in n.body for c in ast.walk(b))
            if not endless or exits:
                out.append((n.lineno, n.end_lineno or n.lineno))
        elif isinstance(n, ast.For):
            if any(isinstance(c, (ast.Break, ast.Return)) for b in n.body for i in ast.walk(b) if isinstance(i, ast.If) for c in ast.walk(i)):
                out.append((n.lineno, n.end_lineno or n.lineno))
    return out


def wait_sites(repo_root: Path, test_dirs: Sequence[str]) -> List[Dict[str, object]]:
    """Every place the test tree waits by the clock, with its milliseconds and enclosing scope.

    `sleep` (`wait_for_timeout`, `time.sleep`) is paid in full on every run,
    except one inside a condition loop: that is `poll-sleep`, which ends when
    the condition holds and costs its interval at most per check
    (facilitation-suite#165: 6 of 11 listed sleeps, the saving overstated 1.75x);
    `page-timer` (a `setTimeout` in an init script or evaluated JS) is paid
    whenever a test waits for its effect; `poll-constant` (`POLL_MS = 15_000`)
    and `long-timeout` (`timeout=` of 10 s or more) mark a test that may wait
    out a real poll or timer. Fix shapes: `page.clock` for a poll-driven test,
    a fetch held until the test releases it for a loading state.
    """
    out: List[Dict[str, object]] = []
    for p in _test_tree_files(repo_root, test_dirs):
        text = p.read_text(encoding="utf-8", errors="replace")
        rel = str(p.relative_to(repo_root)).replace("\\", "/")
        scopes = _scopes(text)
        hits: List[Tuple[int, str, int]] = []
        timers = _js_timer_calls(text)
        noop = {off for off, _ms, cb in timers if _NOOP_CALLBACK_RE.match(cb)}
        hits += [(m.start(), "sleep", int(float(m.group(1).replace("_", "")))) for m in _SLEEP_MS_RE.finditer(text)]
        hits += [(m.start(), "sleep", int(float(m.group(1).replace("_", "")) * 1000)) for m in _SLEEP_S_RE.finditer(text)]
        hits += [(off, "page-timer", ms) for off, ms, _cb in timers]
        hits += [(m.start(), "long-timeout", int(m.group(1).replace("_", ""))) for m in _TIMEOUT_KW_RE.finditer(text)
                 if int(m.group(1).replace("_", "")) >= LONG_TIMEOUT_MS]
        hits += [(m.start(2), "poll-constant", int(m.group(2).replace("_", ""))) for m in _POLL_CONST_RE.finditer(text)
                 if int(m.group(2).replace("_", "")) >= POLL_CONSTANT_MIN_MS]
        loops = _poll_loops(text)
        for off, kind, ms in sorted(hits):
            line = text.count("\n", 0, off) + 1
            if kind == "sleep" and any(a <= line <= b for a, b in loops):
                kind = "poll-sleep"
            inner = [name for a, b, name in scopes if a <= line <= b]
            out.append({"file": rel, "line": line, "kind": kind, "ms": ms, "scope": inner[-1] if inner else None,
                        "unawaited": off in noop, "text": text.splitlines()[line - 1].strip()[:120]})
    return out


SLOW_NODE_FACTOR = 10
SLOW_NODE_MIN_S = 1.0
APP_TIMER_MIN_MS = 1_000
_APP_SKIP_DIRS = frozenset({"node_modules", "_vendored", "vendor", "dist", "build", ".git", ".venv", "venv", "__pycache__"})
_APP_SLEEP_RE = re.compile(r"\bsleep\(\s*([\d_]+)\s*\)")


def slow_nodes(nodes: Dict[str, float]) -> List[Dict[str, object]]:
    """Executed nodes far above the suite's median: the `find the wait` candidates (fleet-config#1157).

    A wait that lives in app source (a 1 s status poll in `poll.js`, waited out
    twice by one test) is invisible to the test-tree scan; the per-node seconds
    show it as an outlier. The first node carries the session boot (its own
    finding), so it is left out. Candidates only: read the test and the app's
    timers (`app_timers`) before pricing one.
    """
    if len(nodes) < 2:
        return []
    items = list(nodes.items())[1:]
    vals = sorted(nodes.values())
    median = vals[len(vals) // 2]
    floor = max(SLOW_NODE_FACTOR * median, SLOW_NODE_MIN_S)
    return [{"nodeid": n, "seconds": s, "median_s": median} for n, s in sorted(items, key=lambda kv: -kv[1]) if s > floor]


_JS_CONST_RE = re.compile(r"^\s*(?:export\s+)?const\s+([A-Z][A-Z0-9_]*)\s*=\s*(\d[\d_]*)\s*;?\s*(?://.*)?$", re.M)


def app_timers(repo_root: Path, test_dirs: Sequence[str]) -> List[Dict[str, object]]:
    """Timers of 1 s or more in the app's own JavaScript: what a test may be waiting out.

    `setTimeout`/`setInterval` (last argument) and `sleep(<ms>)`, longest
    first. A delay that is a named constant (`setInterval(tick, RUNNING_APPS_POLL_MS)`)
    resolves through `const NAME = <ms>` in the same file, else in another
    module's `export const`, and carries its `name`; a name declared nowhere, or
    with different values in other files and none in this one, is left out
    rather than guessed (app-launcher#1375: the 3 s and 4 s polls the slowest
    tests waited out were all named constants). Tests, vendored copies,
    `node_modules` and minified files are left out. A hint to confirm by
    reading, never a cost: only the test's own stubbed endpoints decide
    whether the timer runs (fleet-config#1157).
    """
    skip = {str((repo_root / d.strip("/")).resolve()) for d in test_dirs}
    sources: List[Tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _APP_SKIP_DIRS and str((Path(dirpath) / d).resolve()) not in skip]
        for fn in filenames:
            if fn.endswith((".js", ".mjs")) and not fn.endswith(".min.js"):
                path = Path(dirpath) / fn
                sources.append((path.relative_to(repo_root).as_posix(), path.read_text(encoding="utf-8", errors="replace")))
    consts: Dict[str, Dict[str, int]] = {}
    for rel, text in sources:
        for m in _JS_CONST_RE.finditer(text):
            consts.setdefault(m.group(1), {})[rel] = int(m.group(2).replace("_", ""))

    def resolve(name: str, rel: str) -> Optional[int]:
        if rel in consts.get(name, {}):
            return consts[name][rel]
        values = set(consts.get(name, {}).values())
        return values.pop() if len(values) == 1 else None

    out: List[Dict[str, object]] = []
    for rel, text in sources:
        hits: List[Tuple[int, str, int, Optional[str]]] = []
        for fn, kind in (("setTimeout", "timeout"), ("setInterval", "interval")):
            for off, delay, _cb in _js_timer_args(text, fn):
                if re.fullmatch(r"\d[\d_]*", delay):
                    hits.append((off, kind, int(delay.replace("_", "")), None))
                elif re.fullmatch(r"[A-Z][A-Z0-9_]*", delay) and (ms := resolve(delay, rel)) is not None:
                    hits.append((off, kind, ms, delay))
        hits += [(m.start(), "sleep", int(m.group(1).replace("_", "")), None) for m in _APP_SLEEP_RE.finditer(text)]
        for off, kind, ms, name in hits:
            if ms >= APP_TIMER_MIN_MS:
                line = text.count("\n", 0, off) + 1
                out.append({"file": rel, "line": line, "kind": kind, "ms": ms, "name": name, "text": text.splitlines()[line - 1].strip()[:120]})
    return sorted(out, key=lambda t: (-int(t["ms"]), str(t["file"]), int(t["line"])))  # type: ignore[call-overload]


SLOW_FIXTURE_TIMEOUT_S = 30.0
# Callers whose `timeout=` is seconds even as an int; a Playwright `timeout=` is integer milliseconds.
_SECONDS_API_ROOTS = frozenset({"httpx", "requests", "urllib", "urlopen", "subprocess", "socket"})


def _dotted_root(func) -> str:
    """The leftmost name of a call target (`httpx.get` -> `httpx`, `urlopen` -> `urlopen`), or ''."""
    import ast
    while isinstance(func, ast.Attribute):
        func = func.value
    return func.id if isinstance(func, ast.Name) else ""


def _fixture_info(fn) -> Optional[Tuple[Optional[str], bool]]:
    """`(scope, autouse)` when `fn` carries a `pytest.fixture` decorator, else None."""
    import ast
    for d in fn.decorator_list:
        target = d.func if isinstance(d, ast.Call) else d
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name != "fixture":
            continue
        kws = {k.arg: k.value for k in d.keywords} if isinstance(d, ast.Call) else {}
        scope = kws.get("scope")
        autouse = kws.get("autouse")
        return (scope.value if isinstance(scope, ast.Constant) else None,
                bool(isinstance(autouse, ast.Constant) and autouse.value))
    return None


def slow_fixtures(repo_root: Path, test_dirs: Sequence[str]) -> List[Dict[str, object]]:
    """Fixtures and helpers (never tests) holding a seconds-valued timeout of 30 s or more (fleet-config#1170).

    local-llm-hub's biggest cost was a session fixture: `httpx.get(...,
    timeout=_WARMUP_TIMEOUT)` with `_WARMUP_TIMEOUT = 90.0`, a cold scan of the
    host's own session history that took 40 s of a 76 s run. `waits` reads
    `timeout=` as integer milliseconds, so a seconds timeout never matched and
    4 s of sleeps ranked first. A float literal, or a module constant holding
    one, is seconds; an int is seconds only in a call to `httpx`, `requests`,
    `urlopen`, `subprocess` or `socket` (a Playwright `timeout=45000` is
    milliseconds). The timeout is a ceiling, not the cost: `pytest
    --durations=0` (the `setup` rows) says what the fixture really took, and a
    long `first_node` points here.
    """
    import ast
    out: List[Dict[str, object]] = []
    for p in _test_tree_files(repo_root, test_dirs):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        consts = {n.targets[0].id: n.value.value for n in tree.body
                  if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                  and isinstance(n.value, ast.Constant) and isinstance(n.value.value, (int, float))
                  and not isinstance(n.value.value, bool)}
        rel = p.relative_to(repo_root).as_posix()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name.startswith("test_"):
                continue
            best = 0.0
            for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                for kw in call.keywords:
                    if kw.arg != "timeout":
                        continue
                    val = kw.value.value if isinstance(kw.value, ast.Constant) else consts.get(getattr(kw.value, "id", ""))
                    if isinstance(val, bool) or not isinstance(val, (int, float)):
                        continue
                    if isinstance(val, float) or _dotted_root(call.func) in _SECONDS_API_ROOTS:
                        best = max(best, float(val))
            if best >= SLOW_FIXTURE_TIMEOUT_S:
                info = _fixture_info(fn)
                out.append({"file": rel, "line": fn.lineno, "name": fn.name, "is_fixture": info is not None,
                            "fixture_scope": info[0] if info else None, "autouse": bool(info and info[1]), "timeout_s": best})
    return sorted(out, key=lambda r: (-float(r["timeout_s"]), str(r["file"]), int(r["line"])))  # type: ignore[call-overload]


def rank_waits(sites: List[Dict[str, object]], nodes: Dict[str, float], repo_root: Path) -> List[Dict[str, object]]:
    """Each wait joined to the executed nodes that pay it, ranked by their measured seconds.

    A wait inside a test belongs to that test; one in a module helper or a
    module-level script belongs to every test in the module whose body (with
    the helpers it calls) names that scope; a wait in a conftest or a shared
    helper module is not attributed (`nodes: null`). `paid_s` is the fixed
    cost of a `sleep` or `page-timer` over those nodes; `measured_s` is what
    the nodes took in all, the ceiling on what removing the wait can save.
    """
    by_test: Dict[Tuple[str, str], List[float]] = {}
    for nid, s in nodes.items():
        mod, _, rest = nid.partition("::")
        by_test.setdefault((mod, test_of(rest).split("::")[-1]), []).append(s)
    texts: Dict[str, Dict[str, str]] = {}
    out = []
    for site in sites:
        mod, scope = str(site["file"]), site["scope"]
        name = mod.rsplit("/", 1)[-1]
        row = {**site, "nodes": None, "measured_s": None, "paid_s": None}
        if name.startswith("test_"):
            if mod not in texts:
                path = repo_root / mod
                texts[mod] = _test_texts(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else {}
            tests = texts[mod]
            if scope is None:
                hit = list(tests)
            elif scope in tests:
                hit = [str(scope)]
            else:
                hit = [t for t, body in tests.items() if re.search(rf"\b{re.escape(str(scope))}\b", body)]
            vals = [s for t in hit for s in by_test.get((mod, t), [])]
            row["nodes"], row["measured_s"] = len(vals), round(sum(vals), 1)
            if site["kind"] in ("sleep", "page-timer") and not site.get("unawaited"):
                row["paid_s"] = round(int(site["ms"]) / 1000 * len(vals), 1)  # type: ignore[call-overload]
        out.append(row)
    for r in out:
        r["ceiling"] = r["kind"] in ("long-timeout", "poll-sleep") or bool(r.get("unawaited"))

    def cost(r: Dict[str, object]) -> float:
        """What the wait is known to cost: seconds paid, else the nodes' seconds, else its own length."""
        if r["paid_s"] is not None:
            return float(r["paid_s"])  # type: ignore[arg-type]
        if r["nodes"] is not None:
            return float(r["measured_s"])  # type: ignore[arg-type]
        return int(r["ms"]) / 1000  # type: ignore[call-overload]

    # A fixed sleep or page timer ranks by the seconds it is paid, a poll constant by its nodes' seconds. A
    # `timeout=` or a poll-loop sleep is a ceiling the test may never reach, so it ranks after every wait that is actually paid
    # (task-os#284: a story with 30 s ceilings had 1.5 s of real waits in its 20.9 s).
    return sorted(out, key=lambda r: (bool(r["ceiling"]), -cost(r)))


def runtime_claims(text: str, file: str) -> List[Dict[str, object]]:
    """Runtime figures ("~19 min") that sit near a word about the gate or suite.

    A figure counts only when a gate/suite word is within `CLAIM_WINDOW`
    characters of it, and not when the words just before it make it a
    threshold ("investigate if a run exceeds ~7 min"), a history ("the
    previous ~10 min") or a setting ("default 5 min"). home-automation's
    audit took those, an iCloud `expired after 10 min` and a telemetry cadence
    as runtime claims (fleet-config#1134). On one line only the first figure (its
    headline) and a figure with a gate/suite/browser word in the `SPAN_WORD_WINDOW`
    characters before it count: a history list, a worker-count scenario and a
    phase with no span in the log are noise (app-launcher#1375).
    """
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        kept = 0
        for m in _MINUTES_RE.finditer(line):
            near = line[max(0, m.start() - CLAIM_WINDOW):m.end() + CLAIM_WINDOW]
            if not _RUNTIME_CONTEXT_RE.search(near) or _NOT_A_CLAIM_RE.search(line[max(0, m.start() - 40):m.start()]):
                continue
            # The first figure on a line is its headline; every later one needs a span word right before it, or it is
            # a history, a scenario or a phase the log has no span for (app-launcher#1375: one bullet gave five claims).
            if kept and not _RUNTIME_CONTEXT_RE.search(line[max(0, m.start() - SPAN_WORD_WINDOW):m.start()]):
                continue
            kept += 1
            out.append({"file": file, "line": i, "text": line.strip()[:160], "claimed_min": float(m.group(1))})
    return out


def runtime_drift(repo_root: Path, run: Dict[str, object], load_state: str) -> Dict[str, object]:
    """Runtime figures in CLAUDE.md / README against the run's measured spans.

    The deterministic half only: every figure on a line about the gate or
    suite, with its distance from the nearest measured span (the whole gate or
    one phase). Which span a figure means ("non-e2e ~4 min" vs "full suite ~25
    min") is the judgment layer's call; `candidate` marks a figure more than
    25% from every span. A loaded run gives no verdict.
    """
    wall = _wall_s(run)
    if wall is None:
        return {"status": "unknown", "reason": "the run's wall time is not in the log", "claims": []}
    spans = {"gate": wall / 60.0}
    for ph in run.get("phases") or []:  # type: ignore[union-attr]
        if ph.get("nodes") and ph.get("wall_s"):
            spans[str(ph["name"])[:40]] = float(ph["wall_s"]) / 60.0
    claims: List[Dict[str, object]] = []
    for name in ("CLAUDE.md", "README.md"):
        p = repo_root / name
        if p.is_file():
            claims += runtime_claims(p.read_text(encoding="utf-8", errors="replace"), name)
    for c in claims:
        claimed = float(c["claimed_min"])  # type: ignore[arg-type]
        nearest = min(spans, key=lambda k: abs(claimed - spans[k]) / spans[k] if spans[k] else math.inf)
        rel = (claimed - spans[nearest]) / spans[nearest] if spans[nearest] else None
        c["nearest_span"], c["delta"] = nearest, (round(rel, 2) if rel is not None else None)
        c["candidate"] = rel is not None and abs(rel) > DRIFT_TOLERANCE
    measured = {k: round(v, 1) for k, v in spans.items()}
    if load_state != "quiet":
        return {"status": "unknown", "reason": f"run is {load_state}; a drift verdict needs a quiet run",
                "measured_min": measured, "claims": claims}
    status = "candidates" if any(c["candidate"] for c in claims) else ("none" if claims else "no-claims")
    return {"status": status, "measured_min": measured, "claims": claims}
