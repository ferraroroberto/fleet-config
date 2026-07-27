"""Deterministic operations helper for the fleet chief (fleet-config#445).

The chief's ~18h sweep (2026-07-25/26) re-hand-wrote the same handful of
board/session/issue operations in inline shell + inline Python on every
poll: ~15 board reads (each a freshly typed inline JSON parser), ~12
dispatches, ~10 session-input sends (each re-declaring the same `ssl` +
`urllib` boilerplate), ~8 exchange reads, ~6 multi-repo issue-state loops,
4 session stops. Retyping meant re-breaking: a `curl`-eats-stdin bug, a
`gh`-emoji `UnicodeEncodeError`, a bare `python` blocked by
`venv_discipline`, two recursive greps that hit the 120s timeout, and two
near-misses dispatching into an already-occupied repo.

That last class matters most: chief's safety rails were memory (prose in
`.claude/skills/chief/SKILL.md`), not mechanism. This module makes the
mechanical half real: `refuse_dispatch` is a pure, unit-tested function
that refuses an occupied-repo dispatch, an at/over-cap dispatch, or an
unconfirmed `yolo` — the CLI's `dispatch` subcommand calls it before ever
issuing the POST that would launch a session. The same principle backs
`verify`: during the same sweep a sub-agent overstepped a read-only brief
and shipped a PR on its own initiative, discovered only because the parent
worker happened to raise the alarm. `skills/_lib/dirty_tree_check.py`
already existed for exactly this (fleet-config#247) and was already wired
into four fan-out skills, but not chief — `verify` closes that gap by
wrapping it, so chief re-checks a worker's self-reported state instead of
trusting it.

Talks to the app-launcher webapp (`https://127.0.0.1:8445`, self-signed
cert — loopback bypasses all auth via `BearerTokenMiddleware`) via stdlib
`urllib.request` only — never a `curl` subprocess, so the "curl consumed
the loop's stdin" bug class cannot recur. Every request is routed through
one `_request()` that calls `assert_loopback` first: the tool refuses to
ever become a remote-control surface, even if a future caller passes a
`--base-url` override.

`issues` is one exception: it shells to `gh issue view` (no batched "view
many issues" API exists), one process per ref, `stdin=DEVNULL` +
`creationflags=NO_WINDOW`. The win there is "chief runs one command", not
"one HTTP request" — the multi-repo loop is finally something the tool
owns instead of the model re-typing it. `escalate` is the other: it shells
to `hooks/slack_notify.py` (its own docstring documents exactly this
standalone-CLI usage) rather than re-implementing Slack posting here —
hooks/ and skills/_lib/ are two independent trees by convention, so this
crosses that boundary via subprocess, never an import.

Subcommands
-----------
  board [--base-url URL] [--json]
      The ~12-line phone-readable digest: column counts, one line per live
      session, PR/job cards, the 5h rate-limit line.

  sessions [--base-url URL] [--json]
      Repo occupancy — the "is this repo already busy" question asked
      before every dispatch.

  exchange <sid> [--tail N] [--base-url URL]
      Last assistant text for a live session, tailed to N chars (default
      2000).

  issues <repo#n> [<repo#n> ...] [--owner OWNER]
      One state-table row per ref via `gh issue view`; a per-ref `gh`
      failure becomes an `error:` row rather than aborting the rest.

  dispatch <repo> <number> [--mode start|yolo] [--model M]
           [--yolo-confirmed] [--base-url URL]
      Refuses (exit 1, no POST) on an occupied repo, an at/over-cap
      worker count, or `yolo` without `--yolo-confirmed`; otherwise POSTs
      `/api/board/issues/start` and marks the new session chief-managed
      (`skills/_lib/chief_managed.py`, fleet-config#443) so
      `hooks/notify_on_idle.py` can route its blocked-on-input
      notifications to chief instead of Slack.

  chief-sid [--base-url URL]
      Prints `CHIEF_SID=<sid>` (or `none`) for the live standing chief —
      the lookup `notify_on_idle.py` shells out to before pushing a
      chief-managed worker's notification into chief's own session.

  say <sid> [--file PATH] [--base-url URL]
      Sends `--file`'s content (or stdin) as session input. Never accepts
      prose as a bare CLI arg — `say` is a pure pipe, it never composes
      the brief.

  stop <sid> [--kill] [--base-url URL]
      `quit` by default; `--kill` must be explicit.

  escalate [--file PATH]
      A visibly distinct, higher-priority Slack ping ("chief needs
      Roberto specifically") — forces `--mention` and the `attention`
      category via `hooks/slack_notify.py`, never a routine worker status.

  verify <repo> --expect merged|built [--branch NAME]
         [--default-branch NAME]
      Post-flight dirty-tree check (wraps `skills/_lib/dirty_tree_check.py`,
      fleet-config#247) — run by chief, never by the worker being checked,
      right before a worker's self-reported "shipped ✅" is trusted onward.
      `<repo>` is a fleet-registry name (resolved via
      `skills/_lib/fleet_repo_scan.py`) or a literal path. Exits 1 on
      STATUS=DIRTY so a caller can gate on the exit code, not just parse
      text.

stdlib only, plus the `gh` CLI for `issues`.
"""

from __future__ import annotations

import argparse
import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chief_managed  # noqa: E402
import dirty_tree_check  # noqa: E402
import fleet_repo_scan  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):  # UTF-8 even when stdout is captured (cp1252 fallback)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

DEFAULT_BASE_URL = "https://127.0.0.1:8445"
DEFAULT_OWNER = "ferraroroberto"
DEFAULT_TAIL = 2000

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


# ---- loopback guard (pure) -------------------------------------------------

def assert_loopback(url: str) -> None:
    """Raise if `url`'s host is not a loopback address.

    The single choke point every HTTP call routes through — `--base-url`
    stays overridable (tests point it at a local fixture server) but can
    never point this tool at a non-loopback host, so it can never become a
    remote-control surface.
    """
    host = urllib.parse.urlsplit(url).hostname
    if host is None or host.lower() not in LOOPBACK_HOSTS:
        raise ValueError(f"chief_ops refuses a non-loopback host: {host!r}")


# ---- pure decision logic (unit-tested without network/gh) ------------------

def repo_occupancy(columns: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Lowercased repo -> occupancy info, for every alive, non-external
    session card in `claude_turn` + `your_turn`.

    A dead (`alive: False`) or `external` (state-file-only, unverifiable)
    card never blocks a dispatch — only a live PTY actually holds the repo.
    """
    occ: Dict[str, Dict[str, Any]] = {}
    cards = list(columns.get("claude_turn") or []) + list(columns.get("your_turn") or [])
    for card in cards:
        if not card.get("alive") or card.get("kind") == "external":
            continue
        repo = str(card.get("project") or "").strip().lower()
        if not repo:
            continue
        occ[repo] = {
            "session_id": card.get("session_id"),
            "status": card.get("status"),
            "age_seconds": card.get("age_seconds"),
            "agent": card.get("agent"),
            "label": card.get("label"),
        }
    return occ


def alive_worker_count(columns: Dict[str, Any]) -> int:
    """Alive session cards, excluding the standing chief's own card."""
    cards = list(columns.get("claude_turn") or []) + list(columns.get("your_turn") or [])
    return sum(1 for c in cards if c.get("alive") and c.get("label") != "chief")


def find_chief_session(columns: Dict[str, Any]) -> Optional[str]:
    """The live standing chief's session id, or None.

    Unlike `repo_occupancy` (deduped by repo, for dispatch gating), this
    scans every card without dedup — fleet-config can simultaneously host
    the standing chief *and* an ordinary dev/worker session (both would
    report `project == "fleet-config"`), so only the `label == "chief"`
    card is actually chief. Used by `chief-sid` so `hooks/notify_on_idle.py`
    can push a chief-managed worker's blocked-on-input notification into
    chief's own session instead of Slack (fleet-config#443).
    """
    cards = list(columns.get("claude_turn") or []) + list(columns.get("your_turn") or [])
    for card in cards:
        if card.get("alive") and card.get("label") == "chief":
            sid = card.get("session_id")
            return str(sid) if sid else None
    return None


def refuse_dispatch(
    repo: str,
    mode: str,
    occupancy: Dict[str, Dict[str, Any]],
    alive_count: int,
    worker_cap: int,
    yolo_confirmed: bool,
) -> Optional[str]:
    """The three mechanical refusals. Returns a reason string, or None to
    proceed. Checked in this order: occupied repo, worker cap, unconfirmed
    yolo — any one of them is reason enough to refuse before the others are
    even relevant."""
    repo_key = repo.strip().lower()
    held = occupancy.get(repo_key)
    if held is not None:
        return (
            f"repo already has a live session: {held.get('session_id')} "
            f"({held.get('status')}, agent={held.get('agent')})"
        )
    if alive_count >= worker_cap:
        return f"at/over worker cap: {alive_count}/{worker_cap} alive"
    if mode == "yolo" and not yolo_confirmed:
        return "yolo mode requires --yolo-confirmed"
    return None


def resolve_repo_path(repo: str, repos: Optional[Dict[str, Any]] = None) -> Path:
    """Resolve `repo` to a directory: a literal existing path first, else a
    fleet-registry name.

    `repos` is injectable (`{name: path}`) so this stays unit-testable
    without touching the real `hooks/projects.toml`; the CLI passes
    `fleet_repo_scan.fleet_repos()` when omitted. Case-insensitive name
    match, mirroring `worktree_claim.py`'s tolerant path/name resolution.
    """
    candidate = Path(repo)
    if candidate.is_dir():
        return candidate.resolve()
    table = repos if repos is not None else fleet_repo_scan.fleet_repos()
    if repo in table:
        return Path(table[repo]).resolve()
    lower = repo.lower()
    for name, path in table.items():
        if name.lower() == lower:
            return Path(path).resolve()
    raise ValueError(f"unknown fleet repo: {repo!r}")


def parse_issue_ref(ref: str) -> Tuple[str, int]:
    repo, sep, num = ref.partition("#")
    if not sep or not repo.strip() or not num.strip():
        raise ValueError(f"expected <repo>#<number>, got: {ref!r}")
    return repo.strip(), int(num.strip())


def _fmt_age(seconds: Any) -> str:
    """Compact age like `3m` / `2h` / `1d`. Unparseable input -> `?`."""
    try:
        secs = float(seconds)
    except (TypeError, ValueError):
        return "?"
    if secs < 0:
        return "?"
    if secs < 3600:
        return f"{int(secs // 60)}m"
    if secs < 86400:
        return f"{int(secs // 3600)}h"
    return f"{int(secs // 86400)}d"


def format_board_digest(board: Dict[str, Any]) -> str:
    """The ~12-line phone-readable digest: counts, live sessions, PR/job
    cards, one rate-limit line."""
    cols = board.get("columns") or {}
    lines: List[str] = [
        "backlog={backlog} claude_turn={claude_turn} your_turn={your_turn} "
        "other={other} done={done}".format(
            backlog=len(cols.get("backlog") or []),
            claude_turn=len(cols.get("claude_turn") or []),
            your_turn=len(cols.get("your_turn") or []),
            other=len(cols.get("other") or []),
            done=len(cols.get("done") or []),
        )
    ]

    for card in list(cols.get("claude_turn") or []) + list(cols.get("your_turn") or []):
        sid = str(card.get("session_id") or "")[:8]
        lines.append(
            f"  {card.get('project')}: {card.get('status')} "
            f"age={_fmt_age(card.get('age_seconds'))} agent={card.get('agent')} sid={sid}"
        )

    for card in cols.get("other") or []:
        if card.get("kind") == "job":
            lines.append(f"  job {card.get('job_name')}: {card.get('state')}")
        else:
            lines.append(f"  PR {card.get('repo')}#{card.get('number')}: {card.get('title')}")

    rate_limits = board.get("rate_limits") or {}
    five = rate_limits.get("five_hour") or {}
    lines.append(
        f"rate_limit_5h={five.get('used_percentage')}% resets={five.get('resets_at')}"
    )
    return "\n".join(lines)


def format_occupancy(occupancy: Dict[str, Dict[str, Any]]) -> str:
    if not occupancy:
        return "(no repo currently occupied)"
    lines = []
    for repo, info in sorted(occupancy.items()):
        sid = str(info.get("session_id") or "")[:8]
        lines.append(
            f"{repo}: {info.get('status')} age={_fmt_age(info.get('age_seconds'))} "
            f"agent={info.get('agent')} sid={sid}"
        )
    return "\n".join(lines)


# ---- thin I/O wrappers ------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _request(
    base_url: str,
    path: str,
    method: str = "GET",
    body: Optional[dict] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    assert_loopback(url)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    context = _ssl_context() if url.startswith("https://") else None
    with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def fetch_issue_state(repo: str, number: int, owner: str = DEFAULT_OWNER) -> Dict[str, Any]:
    """One `gh issue view` per ref (no batched API exists). A `gh` failure
    becomes an `error` field rather than raising, so one bad ref never
    aborts the rest of a multi-repo table."""
    res = subprocess.run(
        [
            "gh", "issue", "view", str(number),
            "--repo", f"{owner}/{repo}",
            "--json", "number,title,state,labels,updatedAt",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, check=False,
    )
    if res.returncode != 0:
        return {"repo": repo, "number": number, "error": res.stderr.strip() or "gh error"}
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return {"repo": repo, "number": number, "error": "unparseable gh output"}
    data["repo"] = repo
    return data


def read_brief(file_arg: Optional[str]) -> str:
    """`--file`'s content, or stdin when omitted. `say` never accepts prose
    as a bare CLI arg — it carries the brief the model already composed,
    it never assembles one."""
    if file_arg:
        return Path(file_arg).read_text(encoding="utf-8")
    return sys.stdin.read()


# ---- CLI --------------------------------------------------------------------

def cmd_board(args: argparse.Namespace) -> int:
    board = _request(args.base_url, "/api/board")
    if args.json:
        print(json.dumps(board, indent=2))
    else:
        print(format_board_digest(board))
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    board = _request(args.base_url, "/api/board")
    occupancy = repo_occupancy(board.get("columns") or {})
    if args.json:
        print(json.dumps(occupancy, indent=2))
    else:
        print(format_occupancy(occupancy))
    return 0


def cmd_exchange(args: argparse.Namespace) -> int:
    result = _request(args.base_url, f"/api/board/sessions/{args.sid}/exchange")
    if not result.get("available"):
        print(f"UNAVAILABLE reason={result.get('reason')}")
        return 1
    assistant = result.get("assistant") or {}
    text = str(assistant.get("text") or "")
    print(f"SOURCE={result.get('source')}")
    print(f"TIMESTAMP={assistant.get('timestamp')}")
    print(text[-args.tail:])
    return 0


def cmd_issues(args: argparse.Namespace) -> int:
    for ref in args.refs:
        repo, number = parse_issue_ref(ref)
        data = fetch_issue_state(repo, number, owner=args.owner)
        if "error" in data:
            print(f"{repo}#{number}: error: {data['error']}")
            continue
        labels = ",".join(str(lab.get("name")) for lab in (data.get("labels") or []))
        print(
            f"{repo}#{number}: {data.get('state')} "
            f"\"{data.get('title')}\" [{labels}] updated={data.get('updatedAt')}"
        )
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    board = _request(args.base_url, "/api/board")
    columns = board.get("columns") or {}
    settings = _request(args.base_url, "/api/board/chief/settings")
    worker_cap = int((settings.get("settings") or {}).get("worker_cap") or 3)

    reason = refuse_dispatch(
        args.repo,
        args.mode,
        repo_occupancy(columns),
        alive_worker_count(columns),
        worker_cap,
        args.yolo_confirmed,
    )
    if reason is not None:
        print(f"REFUSED={reason}")
        return 1

    body = {"repo": args.repo, "number": args.number, "mode": args.mode}
    if args.model:
        body["model"] = args.model
    result = _request(args.base_url, "/api/board/issues/start", method="POST", body=body)
    sid = (result.get("session") or {}).get("session_id")
    if sid:
        try:
            chief_managed.mark(str(sid), args.repo, args.number)
        except OSError:
            pass  # best-effort -- a marking failure must never undo a real dispatch
    print(f"DISPATCHED session={sid} repo={args.repo} issue={args.number}")
    return 0


def cmd_chief_sid(args: argparse.Namespace) -> int:
    board = _request(args.base_url, "/api/board")
    sid = find_chief_session(board.get("columns") or {})
    print(f"CHIEF_SID={sid or 'none'}")
    return 0 if sid else 1


def cmd_say(args: argparse.Namespace) -> int:
    text = read_brief(args.file)
    _request(
        args.base_url, f"/api/claude-code/sessions/{args.sid}/input",
        method="POST", body={"data": text, "submit": True},
    )
    print(f"SENT sid={args.sid} chars={len(text)}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    mode = "kill" if args.kill else "quit"
    _request(
        args.base_url, f"/api/claude-code/sessions/{args.sid}/stop",
        method="POST", body={"mode": mode},
    )
    print(f"STOPPED sid={args.sid} mode={mode}")
    return 0


def cmd_escalate(args: argparse.Namespace) -> int:
    """Post a visibly distinct, higher-priority Slack ping — "chief needs
    Roberto specifically", never a routine worker status (fleet-config#443).

    Shells to the existing `hooks/slack_notify.py` transport (its own
    module docstring documents exactly this standalone-CLI usage) rather
    than re-implementing Slack posting here — same cross-tier-via-subprocess
    pattern `hooks/notify_on_idle.py` uses to reach `chief_ops.py`. Routes to
    the `attention` category channel and forces `--mention` regardless of
    the `[global] slack_notify_mention` default, so it reads and *sounds*
    different from a routine ping.
    """
    text = read_brief(args.file)
    message = f"🚨 CHIEF ESCALATION 🚨\n{text}"
    repo_root = Path(__file__).resolve().parent.parent.parent
    slack_notify_path = repo_root / "hooks" / "slack_notify.py"
    proc = subprocess.run(
        [sys.executable, str(slack_notify_path), "--category", "attention",
         "--mention", "--text", message],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        cwd=str(repo_root), check=False,
    )
    ok = proc.returncode == 0
    print(f"ESCALATED={'yes' if ok else 'no'}")
    if not ok:
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
    return 0 if ok else 1


def cmd_verify(args: argparse.Namespace) -> int:
    repo_path = resolve_repo_path(args.repo)
    resolved_default = args.default_branch or dirty_tree_check.detect_default_branch(repo_path)
    current_branch, porcelain_empty, commits_ahead = dirty_tree_check.gather(repo_path, resolved_default)
    result = dirty_tree_check.evaluate(
        args.expect, current_branch, resolved_default, args.branch, porcelain_empty, commits_ahead
    )
    print(f"STATUS={result.status}")
    print(f"BRANCH={current_branch}")
    if result.reason:
        print(f"REASON={result.reason}")
    return 0 if result.status == "CLEAN" else 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="chief_ops", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("board", help="~12-line board digest")
    b.add_argument("--base-url", default=DEFAULT_BASE_URL)
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_board)

    s = sub.add_parser("sessions", help="repo occupancy")
    s.add_argument("--base-url", default=DEFAULT_BASE_URL)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sessions)

    e = sub.add_parser("exchange", help="last assistant text for a live session")
    e.add_argument("sid")
    e.add_argument("--tail", type=int, default=DEFAULT_TAIL)
    e.add_argument("--base-url", default=DEFAULT_BASE_URL)
    e.set_defaults(func=cmd_exchange)

    i = sub.add_parser("issues", help="multi-repo issue-state table")
    i.add_argument("refs", nargs="+", help="<repo>#<number> ...")
    i.add_argument("--owner", default=DEFAULT_OWNER)
    i.set_defaults(func=cmd_issues)

    d = sub.add_parser("dispatch", help="guard-railed issue-start")
    d.add_argument("repo")
    d.add_argument("number", type=int)
    d.add_argument("--mode", choices=("start", "yolo"), default="start")
    d.add_argument("--model", default=None)
    d.add_argument("--yolo-confirmed", action="store_true")
    d.add_argument("--base-url", default=DEFAULT_BASE_URL)
    d.set_defaults(func=cmd_dispatch)

    cs = sub.add_parser("chief-sid", help="find the standing chief's live session id")
    cs.add_argument("--base-url", default=DEFAULT_BASE_URL)
    cs.set_defaults(func=cmd_chief_sid)

    say_p = sub.add_parser("say", help="send input to a live session")
    say_p.add_argument("sid")
    say_p.add_argument("--file", default=None)
    say_p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    say_p.set_defaults(func=cmd_say)

    st = sub.add_parser("stop", help="stop a live session (quit by default)")
    st.add_argument("sid")
    st.add_argument("--kill", action="store_true")
    st.add_argument("--base-url", default=DEFAULT_BASE_URL)
    st.set_defaults(func=cmd_stop)

    esc = sub.add_parser("escalate", help="high-priority Slack ping (fleet-config#443)")
    esc.add_argument("--file", default=None)
    esc.set_defaults(func=cmd_escalate)

    v = sub.add_parser("verify", help="post-flight dirty-tree check (fleet-config#247)")
    v.add_argument("repo")
    v.add_argument("--expect", choices=("merged", "built"), required=True)
    v.add_argument("--branch", default=None)
    v.add_argument("--default-branch", default=None)
    v.set_defaults(func=cmd_verify)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, urllib.error.URLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
