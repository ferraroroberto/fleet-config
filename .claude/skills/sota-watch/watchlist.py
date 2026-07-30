"""Watchlist state for /sota-watch (fleet-config#393).

Two subcommands:

  due   [--today YYYY-MM-DD]
        Read watchlist.toml + the out-of-repo state file and print one
        ``AREA=`` line per area with its computed status:
          AREA=<name>|status=due|last=<date>|next_due=<date>|check_every=<n>
          AREA=<name>|status=fresh|last=<date>|next_due=<date>|check_every=<n>
          AREA=<name>|status=delegated|delegate=<ref>|stale_after=<n>|last=<date>
        plus a ``DUE=<n>`` total. Delegated areas are always reported (the
        skill relays the delegate ledger every run); ``stale_after`` is the
        threshold the SKILL applies to the delegate's own last-run date.

  mark  --area <name> [--date YYYY-MM-DD]
        Record that an area was checked (default: today). Creates the state
        file on first use.

State lives in ``~/.claude/sota-watch/state.json`` — outside the repo, so the
unattended weekly run never needs a commit (same pattern as fleet-health's
ledger). Override the directory with ``SOTA_WATCH_STATE_DIR`` (tests).
Until an area is first marked, its ``verdict_date`` is the baseline.

stdlib + the shared `skills/_lib/utf8_stdio` helper only. Run with the repo venv:
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/sota-watch/watchlist.py due
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

WATCHLIST = Path(__file__).resolve().parent / "watchlist.toml"


def state_dir() -> Path:
    override = os.environ.get("SOTA_WATCH_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude" / "sota-watch"


def state_path() -> Path:
    return state_dir() / "state.json"


def load_watchlist(path: Path = WATCHLIST) -> Dict[str, Dict[str, Any]]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    areas = data.get("areas", {})
    if not areas:
        raise SystemExit(f"❌ no [areas.*] entries in {path}")
    return areas


def load_state(path: Optional[Path] = None) -> Dict[str, str]:
    p = path or state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupt state file must degrade to "everything due", never crash
        # the unattended run — the cost is one extra research pass.
        return {}


def save_state(state: Dict[str, str], path: Optional[Path] = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def last_checked(name: str, cfg: Dict[str, Any], state: Dict[str, str]) -> dt.date:
    """The baseline date for an area: recorded state, else the seed verdict_date."""
    raw = state.get(name)
    if raw:
        return dt.date.fromisoformat(raw)
    seed = cfg.get("verdict_date")
    if isinstance(seed, dt.date):
        return seed
    raise SystemExit(f"❌ area '{name}' has no verdict_date and no recorded state")


def area_line(name: str, cfg: Dict[str, Any], state: Dict[str, str], today: dt.date) -> str:
    every = int(cfg.get("check_every_days", 0))
    if every <= 0:
        raise SystemExit(f"❌ area '{name}' needs a positive check_every_days")
    last = last_checked(name, cfg, state)
    if cfg.get("delegate"):
        return (
            f"AREA={name}|status=delegated|delegate={cfg['delegate']}"
            f"|stale_after={every}|last={last.isoformat()}"
        )
    next_due = last + dt.timedelta(days=every)
    status = "due" if today >= next_due else "fresh"
    return (
        f"AREA={name}|status={status}|last={last.isoformat()}"
        f"|next_due={next_due.isoformat()}|check_every={every}"
    )


def cmd_due(today: dt.date) -> None:
    areas = load_watchlist()
    state = load_state()
    lines = [area_line(name, cfg, state, today) for name, cfg in areas.items()]
    for line in lines:
        print(line)
    print(f"DUE={sum(1 for l in lines if '|status=due|' in l)}")


def cmd_mark(area: str, date: dt.date) -> None:
    areas = load_watchlist()
    if area not in areas:
        raise SystemExit(f"❌ unknown area '{area}' — not in {WATCHLIST.name}")
    state = load_state()
    state[area] = date.isoformat()
    save_state(state)
    print(f"✅ marked {area} checked {date.isoformat()}")


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Watchlist state for /sota-watch.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("due")
    d.add_argument("--today", default=None, help="YYYY-MM-DD (default: today)")

    m = sub.add_parser("mark")
    m.add_argument("--area", required=True)
    m.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")

    args = ap.parse_args(argv)
    if args.cmd == "due":
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
        cmd_due(today)
    elif args.cmd == "mark":
        date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
        cmd_mark(args.area, date)


if __name__ == "__main__":
    main()
