"""The deterministic half of /inspiration-scan: read, dedupe, score, render.

  run       read every configured source, drop items seen on earlier days,
            score the rest through the hub, write the HTML digest and print a
            manifest (DIGEST=, per-type SOURCES= lines, counts, STATUS=).
  init      copy sources.example.toml into the state root if no list exists.
  login-x   open the dedicated X Chrome profile headed so the owner logs in once.

Everything it writes (the source list, `seen.json`, `digests/`) lives in the
machine-local state root, never in the repo. The manifest names source types
and counts only; source names and item text stay in the digest file.

stdlib only; the X reader runs as a subprocess under a Python with Playwright.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SKILL_DIR = Path(__file__).resolve().parent
REPO = SKILL_DIR.parents[2]
AUTOMATION_ROOT = REPO.parent

sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import feeds  # noqa: E402
import render  # noqa: E402
import score  # noqa: E402
import sources  # noqa: E402
from fleet_core.hooks_state import atomic_write_text  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

ensure_utf8_stdio()

# Items scoring below this are dropped as not relevant enough to read.
MIN_SCORE = 6
# Seen ids older than this can no longer reappear inside any lookback window.
SEEN_RETENTION_DAYS = 60
# The profile-lock backoff can wait 15 minutes; each account takes under a minute.
X_BASE_TIMEOUT_S = 900
X_PER_HANDLE_TIMEOUT_S = 60
EXIT_NO_SOURCES = 2
EXIT_BAD_CONFIG = 3


def x_python(settings: sources.Settings) -> Path:
    return Path(settings.x_python) if settings.x_python else (
        AUTOMATION_ROOT / "app-launcher" / ".venv" / "Scripts" / "python.exe")


def x_profile_dir(settings: sources.Settings) -> Path:
    return Path(settings.x_profile_dir) if settings.x_profile_dir else sources.state_root() / "chrome-profile"


def _x_results(x_sources: List[sources.Source], reply: Dict[str, object]) -> List[feeds.SourceResult]:
    by_handle = {str(r.get("handle")): r for r in reply.get("sources", []) if isinstance(r, dict)}
    results = []
    for src in x_sources:
        row = by_handle.get(src.url)
        if row is None:
            results.append(feeds.SourceResult(src, feeds.STATUS_UNKNOWN, "reader returned no result"))
            continue
        items = [feeds.Item(feeds.item_id(p["link"]), sources.KIND_X, src.name, feeds.clean_text(p["text"])[:120],
                            p["link"], feeds.clean_text(p["text"]),
                            feeds.parse_date(p.get("published"))) for p in row.get("items", [])]
        results.append(feeds.SourceResult(src, str(row.get("status", feeds.STATUS_UNKNOWN)),
                                          str(row.get("reason", "")), items))
    return results


def read_x(x_sources: List[sources.Source], settings: sources.Settings,
           since: _dt.datetime) -> List[feeds.SourceResult]:
    """Run the X reader under its Playwright Python; every failure is a named state."""
    if not x_sources:
        return []
    python = x_python(settings)
    if not python.is_file():
        return [feeds.SourceResult(s, feeds.STATUS_UNKNOWN, "no Playwright Python for the X reader")
                for s in x_sources]
    request = {"handles": [s.url for s in x_sources], "profile_dir": str(x_profile_dir(settings)),
               "since": since.isoformat(), "scrolls": settings.x_scrolls}
    timeout = X_BASE_TIMEOUT_S + X_PER_HANDLE_TIMEOUT_S * len(x_sources)
    try:
        proc = subprocess.run([str(python), str(SKILL_DIR / "x_reader.py"), "read"], input=json.dumps(request),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=NO_WINDOW)
        reply = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except subprocess.TimeoutExpired:
        reply = {"error": "timeout"}
    except (OSError, ValueError):
        reply = {"error": "reader-crashed"}
    if "error" in reply:
        status = feeds.STATUS_NOT_READ if reply["error"] == "profile-locked" else feeds.STATUS_UNKNOWN
        return [feeds.SourceResult(s, status, str(reply["error"])) for s in x_sources]
    return _x_results(x_sources, reply)


def load_seen(path: Path) -> Dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"seen ledger unreadable ({type(exc).__name__}); fix or move {path.name}") from exc
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def prune_seen(seen: Dict[str, str], today: _dt.date) -> Dict[str, str]:
    cutoff = (today - _dt.timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    return {k: v for k, v in seen.items() if v >= cutoff}


def select_new(results: List[feeds.SourceResult], seen: Dict[str, str]) -> Tuple[List[feeds.Item], int]:
    """New items across all sources (deduped by id), and how many were already seen."""
    new: Dict[str, feeds.Item] = {}
    dropped = 0
    for result in results:
        for item in result.items:
            if item.id in seen:
                dropped += 1
            elif item.id not in new:
                new[item.id] = item
    return list(new.values()), dropped


def rank(scored: List[score.Scored], top_n: int) -> Tuple[List[score.Scored], List[score.Scored]]:
    kept = sorted((s for s in scored if s.score is not None and s.score >= MIN_SCORE),
                  key=lambda s: s.score or 0, reverse=True)[:top_n]
    return kept, [s for s in scored if s.score is None]


def _manifest(results: List[feeds.SourceResult]) -> List[str]:
    lines = []
    for kind in render.KIND_LABELS:
        mine = [r for r in results if r.source.kind == kind]
        if mine:
            states = {s: sum(1 for r in mine if r.status == s)
                      for s in (feeds.STATUS_OK, feeds.STATUS_NOT_READ, feeds.STATUS_UNKNOWN)}
            lines.append(f"SOURCES={kind}|total={len(mine)}|ok={states['ok']}|not-read={states['not-read']}"
                         f"|unknown={states['unknown']}|items={sum(len(r.items) for r in mine)}")
    return lines


def cmd_run(today: _dt.date) -> int:
    try:
        config = sources.load_config()
    except FileNotFoundError:
        print(f"SOURCES_FILE=missing ({sources.sources_path()}); run `scan.py init` and fill it in")
        print("STATUS=no-sources")
        return EXIT_NO_SOURCES
    except sources.ConfigError as exc:
        print(f"SOURCES_FILE=invalid: {exc}")
        print("STATUS=bad-config")
        return EXIT_BAD_CONFIG
    settings = config.settings
    since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=settings.lookback_days)
    results = [feeds.read_source(s, since) for s in config.sources if s.kind != sources.KIND_X]
    results += read_x([s for s in config.sources if s.kind == sources.KIND_X], settings, since)

    root = sources.state_root()
    seen = prune_seen(load_seen(root / "seen.json"), today)
    new, dropped = select_new(results, seen)
    deferred = max(0, len(new) - settings.max_scored)
    scored, errors = score.score_items(new[:settings.max_scored], score.fleet_profile(AUTOMATION_ROOT),
                                       config.likes, settings.model)
    top, unscored = rank(scored, settings.top_n)
    for entry in scored:
        if entry.score is not None:
            seen[entry.item.id] = today.isoformat()

    digest_dir = root / "digests"
    digest_dir.mkdir(parents=True, exist_ok=True)  # runtime-write-exempt: digests live in the machine-local state root
    digest = digest_dir / f"{today.isoformat()}.html"
    atomic_write_text(digest, render.render(today.isoformat(), results, top, unscored, dropped, errors))
    atomic_write_text(root / "seen.json", json.dumps(seen, indent=0, sort_keys=True))

    for line in _manifest(results):
        print(line)
    print(f"COUNTS=new={len(new)}|seen_before={dropped}|scored={len(scored) - len(unscored)}"
          f"|unscored={len(unscored)}|deferred={deferred}|kept={len(top)}")
    for error in errors:
        print(f"SCORING_ERROR={error}")
    print(f"DIGEST={digest}")
    healthy = not errors and all(r.status == feeds.STATUS_OK for r in results)
    print(f"STATUS={'ok' if healthy else 'degraded'}")
    return 0


def cmd_init() -> int:
    target = sources.sources_path()
    if target.exists():
        print(f"SOURCES_FILE={target} (exists, left unchanged)")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)  # runtime-write-exempt: the machine-local state root
    atomic_write_text(target, (SKILL_DIR / "sources.example.toml").read_text(encoding="utf-8"))
    print(f"SOURCES_FILE={target} (created from the example; edit it)")
    return 0


def cmd_login_x() -> int:
    settings = sources.load_config().settings if sources.sources_path().exists() else sources.Settings()
    python = x_python(settings)
    if not python.is_file():
        print(f"no Playwright Python at {python}; set settings.x_python", file=sys.stderr)
        return 1
    print("A Chrome window opens on the X login page: log in, then close the window.", flush=True)
    # subprocess-timeout-exempt: interactive login; it ends when the owner closes the window
    return subprocess.call([str(python), str(SKILL_DIR / "x_reader.py"), "login",
                            "--profile-dir", str(x_profile_dir(settings))], creationflags=NO_WINDOW)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Daily inspiration digest: read, dedupe, score, render.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="one daily pass")
    run.add_argument("--date", help="run date YYYY-MM-DD (default: today)")
    sub.add_parser("init", help="create the source list from the example")
    sub.add_parser("login-x", help="log into X once in the dedicated Chrome profile")
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init()
    if args.command == "login-x":
        return cmd_login_x()
    today = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    return cmd_run(today)


if __name__ == "__main__":
    sys.exit(main())
