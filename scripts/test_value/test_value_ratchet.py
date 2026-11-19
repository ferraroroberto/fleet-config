"""Ratchet on what a test suite costs and whether each new test unit earns its place.

A test **unit** is a whole check function or a whole test file (a pytest module), never one assertion.
The suite's run history (the JSONL ledger `unit_ledger.py` writes) and the reports of the run being
checked are the inputs; nothing here runs a test except the one coverage run `new-test-value` asks for.
Four rules, each its own failure mode:

  new-test-value  A unit that is not in the baseline at the merge base (new in this diff) shows one of:
                  a `test-value-exempt: <reason>` marker (on its declaration or the comment block above
                  it; for a unit named by its file path, anywhere in the file's header, before its first
                  import, def or class); a red-before-fix record (a ledger row where it failed on a tree
                  other than this one); a covered source line no other unit covers, against the weekly
                  coverage map (one coverage run of only the new units). Otherwise it fails.
  spawn-budget    A unit's process spawns in this run may not exceed its baseline entry; a unit with no
                  entry fails until `--update-baseline --allow-additions` records it, which shows in review.
  flake-ledger    A unit that both passed and failed on one tree hash, under one label, is a flake. It stays
                  on the ledger until it passes `flake_clear_runs` runs in a row afterwards. The number of
                  open flakes may not exceed the baseline's `flake_count`, and none may stay open longer
                  than the baseline's `flake_max_age_days`. Both figures only shrink.
  hermetic        (weekly, `--rules hermetic`) A unit red in the latest fresh-clone run but green in the
                  latest primary run that has it is not hermetic; the baseline's `hermetic` list only shrinks.

Wall-clock time is never a rule: it flakes under box load, so the spawn count stands in for cost.

Exit codes: 0 every rule passed; 1 a rule failed (or the baseline is missing); 2 bad config or arguments;
3 a fact could not be established (no ledger, no report of the run, a missing or stale coverage map, no
merge base, no recent fresh-clone run): NOT CONFIRMED, never a pass. When a rule failed and another
could not be checked, the exit is 1, and both are printed.

Opt-in, per adopter, in `.fleet.toml` (every key but `enabled` optional, defaults shown):

    [test_value_ratchet]
    enabled     = true
    baseline    = ".test-value-ratchet-baseline.json"
    unit_roots  = ["tests"]                            # where units are declared (and their exempt markers)
    unit_runner = ["-m", "pytest", "-q", "-p", "scripts.unit_ledger_pytest"]
                                                       # argv after the interpreter; runs the units
                                                       # `FLEET_GATE_ONLY` names
    base_ref    = "origin/main"                        # the merge base's baseline decides what is new
    label       = "gate"                               # the ledger label of per-change runs
    capture_runs = 3                                   # --update-baseline takes each unit's max over these
    flake_clear_runs = 10
    coverage_map_max_age_days = 8
    hermetic_label = "weekly-hermetic-gate"
    hermetic_max_age_days = 8
    coverage_timeout_s = 1800

Machine-local paths come in as arguments, never from the config. The run being checked is named one of
two ways: by its live reports while the gate is still running (`--units-report`, `--spawns-report`,
`--tree`, from the env `unit_ledger.py` sets), or after it by its ledger run id (`--run-id`, the id the
gate passed to `unit_ledger.py run`). `--probe-dir` puts the spawn probe on `PYTHONPATH` for the
coverage run when the ratchet runs outside the gate's environment.

    test_value_ratchet.py --ledger L --run-id ID --coverage-map M --probe-dir scripts/spawn_probe
    test_value_ratchet.py --ledger L --units-report U --spawns-report S --coverage-map M --tree WTREE
    test_value_ratchet.py --rules hermetic --ledger L
    test_value_ratchet.py --update-baseline [--allow-additions] --ledger L
    test_value_ratchet.py --build-map REPORT --out M --tree WTREE

stdlib only. Contract and adoption: docs/test-value-ratchet.md in project-scaffolding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

Row = dict[str, Any]  # one ledger, report, baseline or map object as read from JSON

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
GIT_TIMEOUT_S = 60
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
PER_CHANGE_RULES = ("new-test-value", "spawn-budget", "flake-ledger")
ALL_RULES = (*PER_CHANGE_RULES, "hermetic")
DEFAULT_MAX_AGE_DAYS = 14
ONLY_ENV = "FLEET_GATE_ONLY"
COVERAGE_REPORT_ENV = "FLEET_GATE_COVERAGE_REPORT"
COVERAGE_ROOT_ENV = "FLEET_GATE_COVERAGE_ROOT"
# A nested coverage run must not write into the outer run's live reports.
OUTER_REPORT_ENVS = ("FLEET_GATE_UNIT_REPORT", "FLEET_GATE_SPAWN_REPORT", "FLEET_GATE_UNIT", "FLEET_GATE_WTREE")
EXEMPT_RE = re.compile(r"test-value-exempt:\s*\S")
HEADER_END_RE = re.compile(r"(?:import|from|def|class|async\s+def)\s")


@dataclass
class Config:
    baseline: str = ".test-value-ratchet-baseline.json"
    unit_roots: tuple[str, ...] = ("tests",)
    unit_runner: tuple[str, ...] = ("-m", "pytest", "-q", "-p", "scripts.unit_ledger_pytest")
    base_ref: str = "origin/main"
    label: str = "gate"
    capture_runs: int = 3
    flake_clear_runs: int = 10
    coverage_map_max_age_days: int = 8
    hermetic_label: str = "weekly-hermetic-gate"
    hermetic_max_age_days: int = 8
    coverage_timeout_s: int = 1800


@dataclass
class Verdict:
    rule: str
    state: str
    lines: list[str] = field(default_factory=list)


class ConfigError(ValueError):
    pass


# ---------------------------------------------------------------- config, baseline, inputs


def load_config(root: Path) -> Config | None:
    """The `[test_value_ratchet]` table, or None when the table is absent or `enabled` is false."""
    try:
        table = tomllib.loads((root / ".fleet.toml").read_text(encoding="utf-8")).get("test_value_ratchet")
    except OSError:
        return None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f".fleet.toml does not parse: {exc}") from exc
    if not isinstance(table, dict) or not table.get("enabled", False):
        return None
    cfg = Config()
    for key, value in table.items():
        if key == "enabled":
            continue
        if not hasattr(cfg, key):
            raise ConfigError(f"[test_value_ratchet] has an unknown key {key!r}")
        default = getattr(cfg, key)
        if isinstance(default, tuple):
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigError(f"[test_value_ratchet] {key} must be a list of strings")
            value = tuple(value)
        elif not isinstance(value, type(default)) or isinstance(value, bool):
            raise ConfigError(f"[test_value_ratchet] {key} must be a {type(default).__name__}")
        setattr(cfg, key, value)
    return cfg


def empty_baseline() -> Row:
    return {"version": 1, "units": {}, "flake_count": 0, "flake_max_age_days": DEFAULT_MAX_AGE_DAYS, "hermetic": []}


def parse_baseline(text: str) -> Row:
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("units"), dict):
        raise ConfigError("baseline has no `units` object")
    base = empty_baseline()
    base.update({k: data[k] for k in base if k in data})
    if not all(isinstance(v, int) and v >= 0 for v in base["units"].values()):
        raise ConfigError("baseline `units` values must be non-negative integers")
    return base


def read_jsonl(path: Path) -> list[Row]:
    rows: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a half-written trailing line must not hide the rest
        if isinstance(row, dict):
            rows.append(row)
    return rows


def read_report(base: Path) -> list[Row]:
    """A probe report: `base` plus the `<base>.<pid>` file each process writes."""
    rows = read_jsonl(base) if base.exists() else []
    for part in sorted(base.parent.glob(base.name + ".*")):
        rows += read_jsonl(part)
    return rows


def _git(root: Path, args: list[str]) -> str | None:
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")  # a read-only check must not leave index locks
    try:
        # git-wrapper-exempt: a stdlib script that ports to other repos; it sets GIT_OPTIONAL_LOCKS=0 itself
        proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, env=env, creationflags=_NO_WINDOW,
                              timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def base_baseline_units(root: Path, cfg: Config, current: Row) -> set[str] | None:
    """Unit names in the baseline at the merge base with `base_ref`; None when that cannot be read.

    A merge base with no baseline file predates the ratchet, so the change that adds it baselines every
    unit it records: `current` stands in."""
    base = _git(root, ["merge-base", "HEAD", cfg.base_ref])
    if base is None or not base.strip():
        return None
    text = _git(root, ["show", f"{base.strip()}:{cfg.baseline}"])
    if text is None:
        return set(current["units"])
    try:
        return set(parse_baseline(text)["units"])
    except (ValueError, ConfigError):
        return None


def _utc(text: object) -> datetime | None:
    try:
        return datetime.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _at(row: Row) -> datetime:
    """The time of a row already filtered to a readable `utc` (the epoch floor is never reached)."""
    return _utc(row.get("utc")) or datetime.min.replace(tzinfo=UTC)


# ---------------------------------------------------------------- new-test-value


def find_exempt(root: Path, cfg: Config, units: Iterable[str]) -> dict[str, bool]:
    """Per unit: is its declaration (a `def <unit>(` or a quoted `"<unit>"`) marked `test-value-exempt:`?

    A unit named by its file path (a pytest module) is marked by the marker in the file's header."""
    wanted = set(units)
    found = {u: False for u in wanted}
    listing = _git(root, ["ls-files", "-z", "--", *cfg.unit_roots]) or ""
    patterns = {u: re.compile(rf"\bdef\s+{re.escape(u)}\s*\(|[\"']{re.escape(u)}[\"']") for u in wanted}
    for rel in filter(None, listing.split("\0")):
        if not rel.endswith(".py"):
            continue
        try:
            lines = (root / rel).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        if rel in found and not found[rel]:
            found[rel] = _header_marked(lines)
        for unit, pattern in patterns.items():
            if found[unit]:
                continue
            for i, line in enumerate(lines):
                if pattern.search(line) and _marked(lines, i):
                    found[unit] = True
                    break
    return found


def _marked(lines: list[str], i: int) -> bool:
    """The marker on line `i` itself, or in the contiguous comment block right above it."""
    if EXEMPT_RE.search(lines[i]):
        return True
    j = i - 1
    while j >= 0 and lines[j].lstrip().startswith("#"):
        if EXEMPT_RE.search(lines[j]):
            return True
        j -= 1
    return False


def _header_marked(lines: list[str]) -> bool:
    """The marker anywhere before the file's first import, def or class (its docstring and comments)."""
    for line in lines:
        if HEADER_END_RE.match(line):
            return False
        if EXEMPT_RE.search(line):
            return True
    return False


def red_first(ledger: list[Row], units: Iterable[str], tree: str) -> dict[str, bool]:
    """Per unit: did the ledger ever record it failing on a tree other than this one?"""
    red = {u: False for u in units}
    for row in ledger:
        unit = row.get("unit")
        if (row.get("kind") == "unit" and unit in red and row.get("status") == FAIL
                and row.get("wtree") and row.get("wtree") != tree):
            red[unit] = True
    return red


def line_key(rel: str, text: str) -> str:
    """A covered line keyed by path and content, so edits elsewhere in the file do not move it."""
    return f"{rel}:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"


def coverage_keys(rows: list[Row], tracked: set[str], cfg: Config) -> dict[str, set[str]]:
    """Per unit, the covered lines of tracked source files outside the unit roots."""
    roots = tuple(r.rstrip("/") + "/" for r in cfg.unit_roots)
    out: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        unit = row.get("unit")
        for entry in row.get("lines") or ():
            if not (isinstance(entry, list) and len(entry) == 3):
                continue
            rel, _lineno, text = entry
            if rel in tracked and not str(rel).startswith(roots):
                out[str(unit)].add(line_key(str(rel), str(text)))
    return out


def load_map(path: Path | None, cfg: Config, now: datetime) -> tuple[set[str] | None, str]:
    """`(covered lines, "")`, or `(None, reason)` for a missing, unreadable or stale map."""
    if path is None:
        return None, "no coverage map was named (--coverage-map)"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "the coverage map does not exist yet: the weekly burst builds it"
    except (OSError, ValueError) as exc:
        return None, f"the coverage map could not be read: {type(exc).__name__}"
    built = _utc(data.get("built_utc")) if isinstance(data, dict) else None
    if built is None or not isinstance(data.get("lines"), list):
        return None, "the coverage map has no build time or no lines"
    if now - built > timedelta(days=cfg.coverage_map_max_age_days):
        return None, (f"the coverage map is stale: built {data['built_utc']}, older than "
                      f"{cfg.coverage_map_max_age_days} days; the weekly burst rebuilds it")
    return set(data["lines"]), ""


def run_coverage(root: Path, cfg: Config, units: list[str],
                 probe_dir: Path | None = None) -> tuple[list[Row] | None, str]:
    """Run only `units` under the coverage probe; `(report rows, "")` or `(None, reason)`.

    `probe_dir` goes first on `PYTHONPATH` when the probe is not there already (the ratchet run after the
    gate rather than inside it)."""
    work = Path(tempfile.mkdtemp(prefix="test_value_cov_"))
    report = str(work / "coverage.jsonl")
    env = {k: v for k, v in os.environ.items() if k not in OUTER_REPORT_ENVS}
    env.update({ONLY_ENV: ",".join(units), COVERAGE_REPORT_ENV: report, COVERAGE_ROOT_ENV: str(root)})
    if probe_dir is not None:
        env["PYTHONPATH"] = str(probe_dir.resolve()) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        proc = subprocess.run([sys.executable, *cfg.unit_runner], cwd=str(root), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                              creationflags=_NO_WINDOW, timeout=cfg.coverage_timeout_s, check=False)
        rows = read_report(Path(report))
    except subprocess.TimeoutExpired:
        return None, f"the coverage run of {', '.join(units)} took over {cfg.coverage_timeout_s}s"
    except OSError as exc:
        return None, f"the coverage run could not start or report: {exc}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if not rows:
        return None, f"the coverage run recorded no lines (exit {proc.returncode}); is the probe on PYTHONPATH?"
    return rows, ""


def rule_new_test_value(root: Path, cfg: Config, baseline: Row, current: list[str],
                        ledger: list[Row] | None, tree: str | None, map_path: Path | None,
                        now: datetime, probe_dir: Path | None = None) -> Verdict:
    v = Verdict("new-test-value", PASS)
    base_units = base_baseline_units(root, cfg, baseline)
    if base_units is None:
        return Verdict(v.rule, UNKNOWN, [f"the baseline at the merge base with {cfg.base_ref} could not be read"])
    new = sorted(u for u in current if u not in base_units)
    if not new:
        v.lines.append("no unit is new against the merge base")
        return v
    exempt = find_exempt(root, cfg, new)
    pending = [u for u in new if not exempt[u]]
    v.lines += [f"{u}: test-value-exempt marker" for u in new if exempt[u]]
    if pending and ledger is None:
        return Verdict(v.rule, UNKNOWN, v.lines + ["the unit ledger is missing, so red-before-fix cannot be read"])
    if pending and not tree:
        return Verdict(v.rule, UNKNOWN, v.lines + ["this run's tree hash is unknown (--tree)"])
    red = red_first(ledger or [], pending, tree or "")
    v.lines += [f"{u}: seen red before the fix" for u in pending if red[u]]
    pending = [u for u in pending if not red[u]]
    if not pending:
        return v
    return _unique_coverage(root, cfg, v, pending, map_path, now, probe_dir)


def _unique_coverage(root: Path, cfg: Config, v: Verdict, pending: list[str], map_path: Path | None,
                     now: datetime, probe_dir: Path | None) -> Verdict:
    covered, why = load_map(map_path, cfg, now)
    if covered is None:
        return Verdict(v.rule, UNKNOWN, v.lines + [f"{', '.join(pending)}: {why}"])
    rows, why = run_coverage(root, cfg, pending, probe_dir)
    if rows is None:
        return Verdict(v.rule, UNKNOWN, v.lines + [why])
    tracked = set(filter(None, (_git(root, ["ls-files", "-z"]) or "").split("\0")))
    per_unit = coverage_keys(rows, tracked, cfg)
    for unit in pending:
        unique = per_unit.get(unit, set()) - covered
        if unique:
            v.lines.append(f"{unit}: covers {len(unique)} line(s) no other unit covers")
        else:
            v.state = FAIL
            v.lines.append(f"{unit}: NEW unit with no red-before-fix record, no unique covered line and no "
                           f"`test-value-exempt: <reason>` marker")
    return v


# ---------------------------------------------------------------- spawn-budget


def rule_spawn_budget(current: list[str], spawns: dict[str, int], baseline: Row) -> Verdict:
    v = Verdict("spawn-budget", PASS)
    for unit in current:
        now = spawns.get(unit, 0)
        entry = baseline["units"].get(unit)
        if entry is None:
            v.state = FAIL
            v.lines.append(f"{unit}: {now} spawns and no baseline entry; record it with "
                           f"--update-baseline --allow-additions")
        elif now > entry:
            v.state = FAIL
            v.lines.append(f"{unit}: {now} spawns, above its baseline of {entry}")
    if v.state == PASS:
        v.lines.append(f"{len(current)} units within their spawn baselines ({sum(spawns.values())} spawns)")
    return v


# ---------------------------------------------------------------- flake-ledger


def open_flakes(ledger: list[Row], label: str, clear_runs: int) -> dict[str, datetime]:
    """Per open flake, the time its episode began. A unit with a pass and a fail on one tree is a flake,
    known from the later of those rows; it closes after `clear_runs` passes in a row past that point."""
    rows = [r for r in ledger if r.get("kind") == "unit" and r.get("label") == label and _utc(r.get("utc"))]
    groups: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in rows:
        if r.get("wtree"):
            groups[(str(r["unit"]), str(r["wtree"]))].append(r)
    seen: dict[str, list[datetime]] = defaultdict(list)
    for (unit, _tree), group in groups.items():
        if {g.get("status") for g in group} >= {PASS, FAIL}:
            seen[unit].append(max(_at(g) for g in group))
    result: dict[str, datetime] = {}
    for unit, flakes in seen.items():
        events = sorted([(t, "flake") for t in flakes]
                        + [(_at(r), r.get("status")) for r in rows if r.get("unit") == unit])
        start: datetime | None = None
        passes = 0
        last_flake: datetime | None = None
        for when, what in events:
            if what == "flake":
                start = start or when
                last_flake, passes = when, 0
            elif start is not None and last_flake is not None and when > last_flake:
                passes = passes + 1 if what == PASS else 0
                if passes >= clear_runs:
                    start, passes = None, 0
        if start is not None:
            result[unit] = start
    return result


def rule_flake_ledger(ledger: list[Row] | None, cfg: Config, baseline: Row, now: datetime) -> Verdict:
    if ledger is None:
        return Verdict("flake-ledger", UNKNOWN, ["the unit ledger is missing, so no flake can be counted"])
    v = Verdict("flake-ledger", PASS)
    flakes = open_flakes(ledger, cfg.label, cfg.flake_clear_runs)
    cap_days = baseline["flake_max_age_days"]
    for unit, start in sorted(flakes.items(), key=lambda p: p[1]):
        age = (now - start).days
        old = age > cap_days
        v.lines.append(f"{unit}: open flake since {start:%Y-%m-%d} ({age} days)" + (
            f", past the {cap_days}-day cap" if old else ""))
        if old:
            v.state = FAIL
    if len(flakes) > baseline["flake_count"]:
        v.state = FAIL
        v.lines.append(f"{len(flakes)} open flakes, above the baseline of {baseline['flake_count']}")
    elif not flakes:
        v.lines.append("no open flake")
    return v


# ---------------------------------------------------------------- hermetic (weekly)


def rule_hermetic(ledger: list[Row] | None, cfg: Config, baseline: Row, now: datetime) -> Verdict:
    if ledger is None:
        return Verdict("hermetic", UNKNOWN, ["the unit ledger is missing"])
    runs = [r for r in ledger if r.get("kind") == "run" and r.get("label") == cfg.hermetic_label]
    last = runs[-1] if runs else None
    when = _utc(last.get("utc")) if last else None
    if last is None or when is None or now - when > timedelta(days=cfg.hermetic_max_age_days):
        return Verdict("hermetic", UNKNOWN, [f"no `{cfg.hermetic_label}` run in the last "
                                             f"{cfg.hermetic_max_age_days} days"])
    red = {str(r["unit"]) for r in ledger if r.get("run_id") == last["run_id"] and r.get("kind") == "unit"
           and r.get("status") == FAIL}
    latest: dict[str, str] = {}
    for r in ledger:
        if r.get("kind") == "unit" and r.get("label") == cfg.label:
            latest[str(r["unit"])] = str(r.get("status"))
    v = Verdict("hermetic", PASS)
    leaky = sorted(u for u in red if latest.get(u) == PASS)
    for unit in leaky:
        known = unit in baseline["hermetic"]
        v.lines.append(f"{unit}: red in a fresh clone, green in the primary checkout" + (" (baselined)" if known else ""))
        if not known:
            v.state = FAIL
    if not leaky:
        v.lines.append(f"every unit green in the primary is green in the fresh clone ({len(red)} red in both)")
    return v


# ---------------------------------------------------------------- baseline capture and the map


def update_baseline(baseline: Row | None, ledger: list[Row], cfg: Config,
                    allow_additions: bool) -> tuple[Row, list[str]]:
    """Shrink (and, with `allow_additions`, extend) the baseline from the last `capture_runs` runs."""
    runs = [r["run_id"] for r in ledger if r.get("kind") == "run" and r.get("label") == cfg.label][-cfg.capture_runs:]
    if not runs:
        raise ConfigError(f"the ledger has no `{cfg.label}` run to capture from")
    observed: dict[str, int] = defaultdict(int)
    for r in ledger:
        if r.get("kind") == "unit" and r.get("run_id") in runs:
            observed[str(r["unit"])] = max(observed[str(r["unit"])], int(r.get("spawns") or 0))
    out = baseline or empty_baseline()
    notes: list[str] = []
    units = dict(out["units"])
    for unit, spawns in observed.items():
        if unit in units:
            units[unit] = min(units[unit], spawns)
        elif allow_additions:
            units[unit] = spawns
            notes.append(f"added {unit}: {spawns} spawns")
    flakes = len(open_flakes(ledger, cfg.label, cfg.flake_clear_runs))
    flake_count = flakes if allow_additions and baseline is None else min(out["flake_count"], flakes)
    hermetic = [u for u in out["hermetic"] if u in observed]
    out = {**out, "units": dict(sorted(units.items())), "flake_count": flake_count, "hermetic": hermetic}
    return out, notes


def build_map(report: Path, out: Path, root: Path, cfg: Config, tree: str | None, now: datetime) -> int:
    rows = read_report(report)
    tracked = set(filter(None, (_git(root, ["ls-files", "-z"]) or "").split("\0")))
    per_unit = coverage_keys(rows, tracked, cfg)
    lines = sorted(set().union(*per_unit.values())) if per_unit else []
    if not lines:
        print("NOT CONFIRMED: the coverage report holds no line of a tracked source file")
        return 3
    data = {"version": 1, "built_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "wtree": tree, "lines": lines,
            "units": {u: len(k) for u, k in sorted(per_unit.items())}}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(out)  # must-use-exempt: a portable stdlib script; one writer per map, the burst
    print(f"coverage map: {len(lines)} covered lines over {len(per_unit)} units")
    return 0


# ---------------------------------------------------------------- main


def _live(units_report: Path | None, spawns_report: Path | None) -> tuple[list[str] | None, dict[str, int]]:
    if units_report is None or not units_report.exists():
        return None, {}
    current = list(dict.fromkeys(str(r["unit"]) for r in read_jsonl(units_report) if r.get("unit")))
    spawns: dict[str, int] = defaultdict(int)
    if spawns_report is not None:
        for r in read_report(spawns_report):
            spawns[str(r.get("unit") or "")] += 1
    return current, dict(spawns)


def _recorded(ledger: list[Row], run_id: str) -> tuple[list[str] | None, dict[str, int], str | None]:
    """The units, per-unit spawns and start tree of one recorded run; `(None, {}, None)` when it has no unit."""
    rows = [r for r in ledger if r.get("run_id") == run_id and r.get("kind") == "unit" and r.get("unit")]
    if not rows:
        return None, {}, None
    current = list(dict.fromkeys(str(r["unit"]) for r in rows))
    spawns = {str(r["unit"]): int(r.get("spawns") or 0) for r in rows}
    return current, spawns, rows[0].get("wtree")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Test-value ratchet: new-test-value, spawn-budget, flake-ledger, hermetic.")
    p.add_argument("--repo", type=Path, default=Path.cwd())
    p.add_argument("--rules", default=",".join(PER_CHANGE_RULES))
    p.add_argument("--ledger", type=Path)
    p.add_argument("--units-report", type=Path)
    p.add_argument("--spawns-report", type=Path)
    p.add_argument("--coverage-map", type=Path)
    p.add_argument("--tree")
    p.add_argument("--run-id", help="check this recorded ledger run instead of the live reports")
    p.add_argument("--probe-dir", type=Path, help="the spawn probe folder to put on PYTHONPATH for the coverage run")
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--allow-additions", action="store_true")
    p.add_argument("--build-map", type=Path, metavar="REPORT")
    p.add_argument("--out", type=Path)
    return p.parse_args(argv)


def _check(args: argparse.Namespace, root: Path, cfg: Config, baseline: Row, now: datetime) -> int:
    rules = [r.strip() for r in args.rules.split(",") if r.strip()]
    if not rules or any(r not in ALL_RULES for r in rules):
        print(f"--rules takes a subset of {', '.join(ALL_RULES)}")
        return 2
    ledger = read_jsonl(args.ledger) if args.ledger and args.ledger.exists() else None
    tree: str | None = args.tree
    if args.run_id:
        current, spawns, tree = _recorded(ledger or [], args.run_id)
        no_report = Verdict("", UNKNOWN, [f"the ledger holds no unit of run {args.run_id}: the gate's test "
                                          f"command reported nothing, or ran outside `unit_ledger.py run`"])
    else:
        current, spawns = _live(args.units_report, args.spawns_report)
        no_report = Verdict("", UNKNOWN, ["no live unit report: run the gate through `unit_ledger.py run` (or a "
                                          "gate-evidence helper wiring the same reports) so each unit and spawn "
                                          "is reported"])
    verdicts: list[Verdict] = []
    for rule in rules:
        if rule == "new-test-value":
            verdicts.append(rule_new_test_value(root, cfg, baseline, current, ledger, tree, args.coverage_map, now,
                                                args.probe_dir)
                            if current is not None else Verdict(rule, UNKNOWN, no_report.lines))
        elif rule == "spawn-budget":
            verdicts.append(rule_spawn_budget(current, spawns, baseline)
                            if current is not None else Verdict(rule, UNKNOWN, no_report.lines))
        elif rule == "flake-ledger":
            verdicts.append(rule_flake_ledger(ledger, cfg, baseline, now))
        else:
            verdicts.append(rule_hermetic(ledger, cfg, baseline, now))
    for v in verdicts:
        tag = {PASS: "OK  ", FAIL: "FAIL", UNKNOWN: "NOT CONFIRMED"}[v.state]
        print(f"{tag} {v.rule}")
        for line in v.lines:
            print(f"    {line}")
    states = {v.state for v in verdicts}
    return 1 if FAIL in states else 3 if UNKNOWN in states else 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    root = args.repo.resolve()
    now = datetime.now(UTC)
    try:
        cfg = load_config(root)
        if cfg is None:
            print("test_value_ratchet: not enabled in .fleet.toml")
            return 0
        path = root / cfg.baseline
        baseline = parse_baseline(path.read_text(encoding="utf-8")) if path.exists() else None
        if args.build_map:
            if not args.out:
                print("--build-map needs --out")
                return 2
            return build_map(args.build_map, args.out, root, cfg, args.tree, now)
        if args.update_baseline:
            if not (args.ledger and args.ledger.exists()):
                print("NOT CONFIRMED: --update-baseline needs the unit ledger (--ledger)")
                return 3
            new, notes = update_baseline(baseline, read_jsonl(args.ledger), cfg, args.allow_additions)
            path.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
            print("\n".join(notes + [f"baseline: {len(new['units'])} units, {new['flake_count']} open flakes"]))
            return 0
    except ConfigError as exc:
        print(f"test_value_ratchet: {exc}")
        return 2
    except (OSError, ValueError) as exc:
        print(f"test_value_ratchet: {type(exc).__name__}: {exc}")
        return 2
    if baseline is None:
        print(f"FAIL baseline: {cfg.baseline} is missing; create it with --update-baseline --allow-additions")
        return 1
    return _check(args, root, cfg, baseline, now)


if __name__ == "__main__":
    # Reports may contain non-ASCII; a cp1252 console would otherwise crash after the verdict.
    for _stream in (sys.stdout, sys.stderr):
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # real console streams are TextIOWrapper
    sys.exit(main())
