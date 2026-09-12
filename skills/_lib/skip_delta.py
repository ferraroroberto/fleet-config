"""Report what a verification run *stopped* running, not just that it passed (fleet-config#846).

Why this exists
----------------
The chief's merge-verification instruction -- verify from a **fresh detached
checkout of the merged default branch**, never a feature branch and never one
of the worktrees -- buys a real guarantee (the merged tree is what ran) at a
cost nothing in the output ever stated: a fresh checkout has none of the repo's
gitignored runtime files, and the tests that need one **skip** instead of
failing. The gate then prints the same green as a run that covered more.

Measured on the 2026-09-12 app-launcher merge round: skips went 17 -> 19
between the primary checkout and the scratch one, and the whole delta was the
`#444` real-agent pin, skipped because a fresh clone has no app registered
under that id (the registry lives in the gitignored
`config/webapp_config.json`). The lane's own words: *"a skip is not a pass."*
Nothing in the gate output named the two tests that stopped running.

This is the fleet's recurring shape one level up -- **a check that could not
establish a fact reporting the same green as one that did**
(`global-CLAUDE.md`, "Verify before declaring done"). The fix is not to abandon
fresh-checkout verification; it is to make the trade *stated* instead of
silent.

What it does
-------------
Pure parser + comparator over **pytest terminal output**. It runs nothing,
spawns nothing and touches no git -- a caller runs its gate however it already
does, saves the output, and hands the text here.

    capture  <output>  [--label NAME] [--out PATH]
        Parse one run into a baseline JSON (skip count + named skips).

    compare  <output>  --baseline PATH
        Parse a second run and report the delta against that baseline.

Both accept `-` for stdin.

Two facts, two verdicts, each with its own unknown
---------------------------------------------------
`compare` reports the *count* fact and the *set* fact separately, because they
fail independently and folding either into the other is the exact defect this
helper exists to close:

    STATUS   SAME | INCREASED | DECREASED | UNKNOWN
             The skip **count** delta. `UNKNOWN` means a run's summary line
             could not be parsed at all, so no count was established -- never
             rendered as "no increase".
    SET      SAME | CHANGED | UNCONFIRMED
             Whether the skipped **tests** are the same ones. `UNCONFIRMED`
             means pytest was not asked to name them (no `-rs`/`-ra`/`-v`), so
             a same-count-different-set coverage loss cannot be ruled out.
    NAMES    complete | partial | none
             How much of each run's skip set could be named.

`INCREASED` or `CHANGED` is the loud outcome: the run covered less than the
baseline, and every `NEW=` line names a test that stopped running.

Report-only, always exits 0 -- callers key on `STATUS`/`SET`, the same contract
as `dirty_tree_check.py`. Pure logic, unit-tested in `tests/test_skip_delta.py`.
stdlib only (matches the `_lib` module contract).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

SCHEMA = 1

# pytest's terminal summary line: "=== 3 failed, 120 passed, 19 skipped in 42.15s ===".
# A candidate needs both an outcome count and a duration, so a test name that
# happens to contain "2 skipped" cannot be mistaken for the summary.
_OUTCOME_RE = re.compile(
    r"\b(\d+)\s+(passed|failed|skipped|error|errors|xfailed|xpassed|deselected"
    r"|warning|warnings|rerun|reruns)\b"
)
_DURATION_RE = re.compile(r"\bin\s+\d+(?:\.\d+)?s\b")
_SKIPPED_COUNT_RE = re.compile(r"\b(\d+)\s+skipped\b")

# Short-summary block, printed by `-rs` / `-ra` / `-rA`:
#   "SKIPPED [2] tests/e2e/test_terminal_reconnect.py:197: could not launch PTY session"
# The location itself contains a colon, so match `<path>:<line>` explicitly
# before falling back to a plain first-colon split.
_SUMMARY_SKIP_RES = (
    re.compile(r"^SKIPPED\s+\[(\d+)\]\s+(.+?:\d+):\s*(.*)$"),
    re.compile(r"^SKIPPED\s+\[(\d+)\]\s+(.+?):\s+(.*)$"),
)
_SUMMARY_SKIP_BARE_RE = re.compile(r"^SKIPPED\s+\[(\d+)\]\s+(.+)$")

# Per-test line, printed by `-v`: "tests/foo.py::test_bar SKIPPED (reason)".
_VERBOSE_SKIP_RE = re.compile(r"^(\S+::\S+)\s+SKIPPED(?:\s*\((.*)\))?")


class Skip(NamedTuple):
    location: str
    reason: str
    count: int


class Run(NamedTuple):
    total: Optional[int]  # None when no summary line was parseable
    named: Tuple[Skip, ...]
    summary_line: str
    reason: str  # why `total` is None; "" otherwise

    @property
    def named_count(self) -> int:
        return sum(s.count for s in self.named)

    @property
    def names_complete(self) -> bool:
        return self.total is not None and self.named_count == self.total


class Delta(NamedTuple):
    status: str  # SAME | INCREASED | DECREASED | UNKNOWN
    set_status: str  # SAME | CHANGED | UNCONFIRMED
    names: str  # complete | partial | none
    delta: Optional[int]
    new: Tuple[Skip, ...]  # `count` is the *increase* at that location
    reason: str


# ---- parsing (pure) --------------------------------------------------------


def _normalize_location(raw: str) -> str:
    """Windows backslashes -> forward slashes, so a primary-checkout baseline
    compares against a scratch-checkout run captured on the same machine."""
    return raw.strip().replace("\\", "/")


def _record(agg: Dict[str, List], location: str, reason: str, count: int) -> None:
    entry = agg.setdefault(location, [0, reason])
    entry[0] += count
    if not entry[1] and reason:
        entry[1] = reason


def _finish(agg: Dict[str, List]) -> Tuple[Skip, ...]:
    return tuple(
        Skip(location=loc, reason=reason, count=count)
        for loc, (count, reason) in sorted(agg.items())
    )


def _extract_summary_skips(lines: List[str]) -> Tuple[Skip, ...]:
    agg: Dict[str, List] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("SKIPPED"):
            continue
        for pattern in _SUMMARY_SKIP_RES:
            match = pattern.match(stripped)
            if match:
                _record(agg, _normalize_location(match.group(2)),
                        match.group(3).strip(), int(match.group(1)))
                break
        else:
            bare = _SUMMARY_SKIP_BARE_RE.match(stripped)
            if bare:
                _record(agg, _normalize_location(bare.group(2)), "", int(bare.group(1)))
    return _finish(agg)


def _extract_verbose_skips(lines: List[str]) -> Tuple[Skip, ...]:
    agg: Dict[str, List] = {}
    for line in lines:
        match = _VERBOSE_SKIP_RE.match(line.strip())
        if match:
            _record(agg, _normalize_location(match.group(1)), (match.group(2) or "").strip(), 1)
    return _finish(agg)


def _parse_named_skips(lines: List[str]) -> Tuple[Skip, ...]:
    """Aggregate named skips by location, `-rs` short-summary form preferred.

    Precedence matters: a `-rA`-style run prints both spellings of the same
    skip, so counting them together would double every entry. The short-summary
    block already carries its own multiplicity (`[2]`), so it wins outright and
    the verbose per-test form is only consulted when it is the sole source.
    """
    for extract in (_extract_summary_skips, _extract_verbose_skips):
        found = extract(lines)
        if found:
            return found
    return ()


def _parse_summary_line(lines: List[str]) -> Tuple[Optional[int], str, str]:
    """Last pytest summary line -> (skip count, that line, failure reason).

    A run that collected nothing ("no tests ran in 0.01s") has no outcome
    counts and so yields no candidate: the count is `None`, not `0`. A run that
    never reached a summary at all established nothing about its skips, and
    saying `0` there would be this helper's own bug in miniature.
    """
    candidate = ""
    for line in lines:
        stripped = line.strip()
        if _DURATION_RE.search(stripped) and _OUTCOME_RE.search(stripped):
            candidate = stripped
    if not candidate:
        return None, "", "no pytest summary line found -- the skip count was never established"
    match = _SKIPPED_COUNT_RE.search(candidate)
    # Drop pytest's `=` padding: it is echoed after a `KEY=` prefix, where the
    # run of equals signs reads as part of the key rather than the value.
    return (int(match.group(1)) if match else 0), candidate.strip("= ").strip(), ""


def parse_pytest_output(text: str) -> Run:
    lines = text.splitlines()
    total, summary_line, reason = _parse_summary_line(lines)
    return Run(total=total, named=_parse_named_skips(lines),
               summary_line=summary_line, reason=reason)


# ---- comparison (pure) -----------------------------------------------------


def _names_verdict(baseline: Run, current: Run) -> str:
    if baseline.names_complete and current.names_complete:
        return "complete"
    if baseline.named or current.named:
        return "partial"
    return "none"


def compare(baseline: Run, current: Run) -> Delta:
    names = _names_verdict(baseline, current)
    base_counts = {s.location: s.count for s in baseline.named}
    new = tuple(
        Skip(location=s.location, reason=s.reason, count=s.count - base_counts.get(s.location, 0))
        for s in current.named
        if s.count > base_counts.get(s.location, 0)
    )

    if baseline.total is None or current.total is None:
        which = "baseline" if baseline.total is None else "current"
        detail = baseline.reason if baseline.total is None else current.reason
        return Delta(status="UNKNOWN", set_status="UNCONFIRMED", names=names, delta=None,
                     new=new, reason=f"{which} run: {detail}")

    delta = current.total - baseline.total
    status = "INCREASED" if delta > 0 else "DECREASED" if delta < 0 else "SAME"

    # A named new skip is a fact even when the naming is partial. Its *absence*
    # is only a fact when both runs named their whole set -- otherwise the sets
    # are simply unconfirmed, never quietly "same".
    if new:
        set_status, reason = "CHANGED", ""
    elif names == "complete":
        set_status, reason = "SAME", ""
    else:
        set_status = "UNCONFIRMED"
        reason = ("pytest was not asked to name its skips (add -rs); a "
                  "same-count-different-set coverage loss cannot be ruled out")
    return Delta(status=status, set_status=set_status, names=names, delta=delta,
                 new=new, reason=reason)


# ---- serialization ---------------------------------------------------------


def run_to_dict(run: Run, label: str) -> dict:
    return {
        "schema": SCHEMA,
        "label": label,
        "total_skipped": run.total,
        "summary_line": run.summary_line,
        "names_complete": run.names_complete,
        "named": [{"location": s.location, "reason": s.reason, "count": s.count}
                  for s in run.named],
        "reason": run.reason,
    }


def run_from_dict(data: dict) -> Run:
    schema = data.get("schema")
    if schema != SCHEMA:
        raise ValueError(f"unsupported baseline schema {schema!r} (expected {SCHEMA})")
    named = tuple(
        Skip(location=entry["location"], reason=entry.get("reason", ""), count=int(entry["count"]))
        for entry in data.get("named", ())
    )
    total = data.get("total_skipped")
    return Run(total=None if total is None else int(total), named=named,
               summary_line=data.get("summary_line", ""), reason=data.get("reason", ""))


# ---- CLI -------------------------------------------------------------------


def _read(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8", errors="replace")


def cmd_capture(source: str, label: str, out: Optional[Path]) -> None:
    run = parse_pytest_output(_read(source))
    payload = json.dumps(run_to_dict(run, label), indent=2, ensure_ascii=False)
    if out is None:
        print(payload)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    # Status goes to stderr so `capture` can pipe its JSON straight to a file.
    print(f"STATUS={'CAPTURED' if run.total is not None else 'UNKNOWN'}", file=sys.stderr)
    print(f"SKIPPED={'' if run.total is None else run.total}", file=sys.stderr)
    print(f"NAMED={run.named_count}", file=sys.stderr)
    if run.reason:
        print(f"REASON={run.reason}", file=sys.stderr)


def cmd_compare(source: str, baseline_path: Path) -> None:
    try:
        baseline = run_from_dict(json.loads(baseline_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # An unreadable baseline is its own unknown: nothing was compared, so
        # nothing may be reported as unchanged.
        print("STATUS=UNKNOWN")
        print("SET=UNCONFIRMED")
        print("NAMES=none")
        print(f"REASON=baseline unusable ({baseline_path}): {exc}")
        return

    current = parse_pytest_output(_read(source))
    result = compare(baseline, current)

    print(f"STATUS={result.status}")
    print(f"SET={result.set_status}")
    print(f"NAMES={result.names}")
    print(f"BASELINE_SKIPPED={'' if baseline.total is None else baseline.total}")
    print(f"CURRENT_SKIPPED={'' if current.total is None else current.total}")
    print(f"DELTA={'' if result.delta is None else format(result.delta, '+d')}")
    for skip in result.new:
        suffix = f": {skip.reason}" if skip.reason else ""
        print(f"NEW={skip.location} (+{skip.count}){suffix}")
    print(f"BASELINE_SUMMARY={baseline.summary_line}")
    print(f"CURRENT_SUMMARY={current.summary_line}")
    if result.reason:
        print(f"REASON={result.reason}")


def main(argv: Optional[List[str]] = None) -> None:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(
        description="Report the skip delta between two pytest runs (fleet-config#846).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="parse one pytest run into a baseline JSON")
    cap.add_argument("output", help="pytest output file, or - for stdin")
    cap.add_argument("--label", default="", help="what this run was (e.g. 'primary checkout')")
    cap.add_argument("--out", type=Path, default=None, help="write here instead of stdout")

    cmp_ = sub.add_parser("compare", help="report a second run's skip delta against a baseline")
    cmp_.add_argument("output", help="pytest output file, or - for stdin")
    cmp_.add_argument("--baseline", type=Path, required=True)

    args = ap.parse_args(argv)
    if args.cmd == "capture":
        cmd_capture(args.output, args.label, args.out)
    else:
        cmd_compare(args.output, args.baseline)


if __name__ == "__main__":
    main()
