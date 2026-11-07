"""Send a file to the owner's chat view from any coding agent (fleet-config#1376).

The agent half of app-launcher#1530. Claude has a native `SendUserFile` tool;
Codex, Grok, Pi and Copilot do not, but every harness can run a shell command.
This script POSTs the path to the launcher's loopback-only outbox endpoint for
the caller's own session -- or, inside a scheduled launcher Job (no session),
for the Job's own running run (app-launcher#2207) -- and prints exactly one
result line:

    SENT=<entry id>            exit 0 -- the launcher accepted and stored the file
    REFUSED=<reason>: <msg>    exit 1 -- the launcher answered and said no
    UNAVAILABLE=<reason>       exit 2 -- no answer, so nothing is known to be sent

The launcher owns every rule about the file (allowed roots, size, type, content
sniffing); this script passes the path through and relays the reason. A reply
it cannot read as an accepted entry is `UNAVAILABLE`, never `SENT`. A timeout is
likewise `UNAVAILABLE`: the launcher may or may not have stored the file.

Usage:
    E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/send_file.py <path> [--caption "..."]

Needs `APP_LAUNCHER_SESSION_ID` (set in every launcher-spawned session) or, in a
Job, `APP_LAUNCHER_JOB_ID` + `APP_LAUNCHER_JOB_RUN_ID`; a session id wins when
both are present. Never
send secrets, or anything bound for GitHub. stdlib only; the HTTP plumbing is
`chief_ops._request`, so the loopback guard and the self-signed-cert handling
are the same ones every launcher client uses.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chief_ops  # noqa: E402

EXIT_SENT, EXIT_REFUSED, EXIT_UNAVAILABLE = 0, 1, 2
# A file is copied into the outbox before the launcher answers (<= 15 MB).
SEND_TIMEOUT = 30.0
# 503 from the launcher: the session-host (which knows the live sessions) is down.
SESSION_HOST_UNREACHABLE = "session_host_unreachable"
# Stamped by the launcher's Job executor on a running Job's child (app-launcher#2207).
JOB_ID_ENV_VAR = "APP_LAUNCHER_JOB_ID"
JOB_RUN_ID_ENV_VAR = "APP_LAUNCHER_JOB_RUN_ID"


def _error_detail(exc: urllib.error.HTTPError) -> Optional[Dict[str, Any]]:
    """The launcher's `{"reason", "message"}` detail, or None for any other body
    (e.g. FastAPI's bare `{"detail": "Not Found"}` from a launcher with no such route)."""
    try:
        detail = json.loads(exc.read().decode("utf-8")).get("detail")
    except (ValueError, AttributeError, OSError):
        return None
    if isinstance(detail, dict) and isinstance(detail.get("reason"), str):
        return detail
    return None


def job_identity(env: Optional[Dict[str, str]] = None) -> Optional[Tuple[str, str]]:
    """`(job id, run id)` of the scheduled Job run this process is, or None outside one."""
    source = os.environ if env is None else env
    job_id = (source.get(JOB_ID_ENV_VAR) or "").strip()
    run_id = (source.get(JOB_RUN_ID_ENV_VAR) or "").strip()
    return (job_id, run_id) if job_id and run_id else None


def classify_error(status: int, detail: Optional[Dict[str, Any]], via_job: bool = False) -> Tuple[int, str]:
    """`(exit code, result line)` for an HTTP error status plus its parsed detail."""
    if detail is None:
        if status == 404 and via_job:
            return EXIT_UNAVAILABLE, "UNAVAILABLE=job-outbox-missing (the launcher has no job outbox route; update it)"
        if status == 404:
            return EXIT_UNAVAILABLE, "UNAVAILABLE=endpoint-missing (the launcher has no outbox route; update it)"
        return EXIT_UNAVAILABLE, f"UNAVAILABLE=unexpected-http-{status}"
    reason = detail["reason"]
    if reason == SESSION_HOST_UNREACHABLE:
        return EXIT_UNAVAILABLE, f"UNAVAILABLE={reason} ({detail.get('message') or 'session-host not answering'})"
    message = str(detail.get("message") or "").strip()
    return EXIT_REFUSED, f"REFUSED={reason}" + (f": {message}" if message else "")


def classify_success(reply: Any) -> Tuple[int, str]:
    """A 2xx counts as sent only when it carries the stored entry's id."""
    reply = reply if isinstance(reply, dict) else {}
    entry = reply.get("entry")
    entry_id = entry.get("id") if isinstance(entry, dict) else None
    if reply.get("ok") is True and isinstance(entry_id, str) and entry_id:
        return EXIT_SENT, f"SENT={entry_id}"
    return EXIT_UNAVAILABLE, "UNAVAILABLE=unexpected-reply (the launcher answered without a stored entry)"


def send(
    path: str,
    caption: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    base_url: str = chief_ops.DEFAULT_BASE_URL,
) -> Tuple[int, str]:
    """POST `path` to the caller's session outbox, or its Job run's outbox when it has no session.

    Returns `(exit code, result line)`.
    """
    sid = chief_ops.caller_session_id(env)
    job = None if sid else job_identity(env)
    if sid:
        route = f"/api/claude-code/sessions/{sid}/outbox"
    elif job:
        route = "/api/jobs/{}/runs/{}/outbox".format(*(urllib.parse.quote(part, safe="") for part in job))
    else:
        return EXIT_UNAVAILABLE, "UNAVAILABLE=no-launcher-session (neither APP_LAUNCHER_SESSION_ID nor a Job run identity is set)"
    body: Dict[str, Any] = {"path": os.path.abspath(path)}
    if caption:
        body["caption"] = caption
    try:
        reply = chief_ops._request(base_url, route, method="POST", body=body, timeout=SEND_TIMEOUT)
    except urllib.error.HTTPError as exc:
        return classify_error(exc.code, _error_detail(exc), via_job=job is not None)
    except TimeoutError:
        return EXIT_UNAVAILABLE, f"UNAVAILABLE=no-reply-within-{SEND_TIMEOUT:.0f}s (the file may or may not have been sent)"
    except urllib.error.URLError as exc:
        return EXIT_UNAVAILABLE, f"UNAVAILABLE=launcher-unreachable ({exc.reason})"
    except json.JSONDecodeError:
        return EXIT_UNAVAILABLE, "UNAVAILABLE=unexpected-reply (the launcher's answer was not JSON)"
    except ValueError as exc:  # chief_ops.assert_loopback: only this PC's launcher is ever addressed
        return EXIT_UNAVAILABLE, f"UNAVAILABLE=bad-base-url ({exc})"
    return classify_success(reply)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Send a file to the owner's chat view (app-launcher outbox).")
    parser.add_argument("path", help="file to send (relative paths resolve against the current directory)")
    parser.add_argument("--caption", default=None, help="short caption shown with the file")
    parser.add_argument("--base-url", default=chief_ops.DEFAULT_BASE_URL, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    code, line = send(args.path, args.caption, base_url=args.base_url)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
