"""Every read and write the chief makes against the app-launcher webapp,
plus the one `gh issue view` per issue ref.

All HTTP goes through `_request`, which calls `assert_loopback` first: the tool
can never address anything but this PC's launcher, even with a `--base-url`
override. The scheme in a base URL is only a hint: every call goes over
whichever scheme the launcher's port serves (`launcher_scheme.py`). stdlib
`urllib` only, never a `curl` subprocess. A transport failure is reported as
its own state by each wrapper, never folded into a verdict.
"""

from __future__ import annotations

import http.client
import json
import subprocess
import time
import urllib.error
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import git_run

from . import launcher_scheme, rails

# The scheme here is a hint; `launcher_scheme.resolve` swaps in the one the port serves.
DEFAULT_BASE_URL = "https://127.0.0.1:8445"
DEFAULT_OWNER = "ferraroroberto"

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Default per-request budget; the `UNCONFIRMED=` line names it.
REQUEST_TIMEOUT = 10.0

# `/api/jobs` shells out to schtasks per job: ~4 s cold, well under 1 s warm.
JOBS_TIMEOUT = 30.0

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


def is_timeout(exc: BaseException) -> bool:
    """True for a read timeout (raw `TimeoutError`) or a connect timeout
    (`URLError` wrapping one)."""
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        exc = exc.reason if isinstance(exc.reason, BaseException) else exc
    return isinstance(exc, TimeoutError)


def transport_failure(exc: BaseException) -> Optional[Tuple[str, int]]:
    """`(stdout line, exit code)` for a launcher call that got no answer, or
    None for anything else (an HTTP status, a bad body) so it keeps its own
    handling.

    Distinct states, never folded together: `UNCONFIRMED` -- the launcher
    stayed silent past the budget or dropped the connection, so a write may
    still have landed -- `UNREACHABLE` -- the connection itself failed, so
    nothing was sent -- and `WRONG_SCHEME` -- the port answered in the other
    protocol, so the request was never handled and the next call uses the
    scheme it serves. An HTTP status is the launcher refusing, handled by
    each wrapper.
    """
    if is_timeout(exc):
        return f"UNCONFIRMED=timeout after {REQUEST_TIMEOUT:g}s", 1
    if isinstance(exc, launcher_scheme.WrongSchemeError):
        return f"WRONG_SCHEME={exc.reason}", 2
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, http.client.HTTPException)):
        return f"UNCONFIRMED=connection dropped with no answer ({type(exc).__name__})", 1
    if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError):
        return f"UNREACHABLE={exc.reason}", 2
    return None


# ---- bounded recovery from a transient transport failure --------------------

# Seconds to wait after the 1st, 2nd and 3rd failed attempt before looking again. A launcher
# restart or a brief session-host outage clears inside this window; a longer one is the
# operator's to handle.
RECOVERY_BACKOFF = (1.0, 2.0, 5.0)
RECOVERY_ATTEMPTS = len(RECOVERY_BACKOFF)

# What a failed call proves about the request, for `transient_failure`: `NOT_SENT` — the
# connection was refused or unreachable, or the host said 503 "nothing was sent", so no byte
# of the request was handled; `MAYBE_SENT` — a timeout or a reset after the request went
# out, so a write may have landed.
NOT_SENT = "not_sent"
MAYBE_SENT = "maybe_sent"


def transient_failure(exc: BaseException) -> Optional[str]:
    """`NOT_SENT` / `MAYBE_SENT` for a failure worth a bounded retry, else None.

    An HTTP status other than 503 is the launcher answering, so it is never transient: a
    502/409/400/403 is an authoritative negative and a 404/500 is not ours to retry.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return NOT_SENT if exc.code == 503 else None
    if isinstance(exc, urllib.error.URLError):
        return transient_failure(exc.reason) if isinstance(exc.reason, BaseException) else NOT_SENT
    if isinstance(exc, (TimeoutError, ConnectionResetError, ConnectionAbortedError)):
        return MAYBE_SENT
    if isinstance(exc, OSError):  # refused, host/network unreachable, name not resolved
        return NOT_SENT
    return None


def request_with_recovery(base_url: str, path: str) -> Dict[str, Any]:
    """`_request` for a GET, retried across `RECOVERY_BACKOFF` while the failure is transient.
    The last failure is raised; a read has no side effect, so retrying it is always safe."""
    for attempt, delay in enumerate(RECOVERY_BACKOFF, start=1):
        try:
            return _request(base_url, path)
        except (OSError, ValueError) as exc:
            if attempt == RECOVERY_ATTEMPTS or transient_failure(exc) is None:
                raise
            time.sleep(delay)
    raise AssertionError("unreachable: the last attempt returns or raises")


# ---- launcher reads behind one command each ---------------------------------

def resolve_sid_via_board(base_url: str, sid: str) -> Tuple[Optional[str], Optional[str]]:
    """`resolve_session_id` against a freshly-fetched board.

    Every operator-facing command that takes a `sid` has to do this, because
    the only place an operator *gets* a sid is `board`/`sessions`, and both
    print the 8-char prefix (`:498`, `:523`) — never the full 32-char id.
    `exchange` resolved it; `say` and `stop` did not, so `say <prefix>` POSTed
    to a session id that does not exist and `say --verify` then read the
    resulting empty card as **pending** — "delivery likely, unconfirmed" for a
    steer that was never accepted at all.

    A board that cannot be read is not evidence that the prefix is wrong, so
    the id passes through unchanged and the real endpoint gets to answer —
    the same never-invent-a-verdict rule `resolve_session_id`'s no-match branch
    already follows. A launcher that is briefly down is waited out first
    (`request_with_recovery`), so a prefix is still resolved once it is back.
    """
    try:
        board = request_with_recovery(base_url, "/api/board")
    except (urllib.error.URLError, ValueError):
        # Only what this read always passed through: a raw timeout or reset still
        # escapes to the caller's `UNCONFIRMED=` line instead of reading as "no match".
        return sid, None
    return rails.resolve_session_id(sid, board.get("columns") or {})


def fetch_running_fleet_jobs(base_url: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """(running fleet-wide job ids, None) or (None, why unreadable).

    Never raises: a jobs API that can't be read is its own `unknown` state,
    never folded into "none running".
    """
    try:
        return rails.running_fleet_jobs(_request(base_url, "/api/jobs", timeout=JOBS_TIMEOUT)), None
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"



def fetch_context(base_url: str, sid: str) -> Dict[str, Any]:
    return _request(base_url, f"/api/claude-code/sessions/{sid}/context")


def fetch_chief_settings(base_url: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """The launcher's `{"auto_compact_threshold": int}` -> `(settings, "")`,
    or `(None, why)` -- a launcher without the endpoint (404) is not the same
    fact as one that could not be reached."""
    try:
        result = _request(base_url, "/api/board/chief-settings")
    except urllib.error.HTTPError as exc:
        return None, f"launcher has no chief-settings endpoint (HTTP {exc.code})"
    except (urllib.error.URLError, ValueError):
        return None, "launcher unreachable"
    return (result, "") if isinstance(result, dict) else (None, "chief-settings reply was not an object")


def fetch_codex_catalog(base_url: str) -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """`(the launcher's Codex model catalog, "")` from `/api/config`, or
    `(None, why)`. The catalog is the one source of the slugs the Board
    accepts, so an unreadable one is its own state, never an empty list."""
    try:
        config = _request(base_url, "/api/config")
    except (OSError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    catalog = (config.get("model_catalog") or {}).get("codex") if isinstance(config, dict) else None
    if not isinstance(catalog, list):
        return None, "launcher config carries no Codex model catalog"
    return catalog, ""


# ---- the transport and the session-input wrappers ---------------------------

def _request(
    base_url: str,
    path: str,
    method: str = "GET",
    body: Optional[dict] = None,
    timeout: float = REQUEST_TIMEOUT,
) -> Dict[str, Any]:
    """One JSON call to the launcher, over the scheme its port serves (`launcher_scheme.request`)."""
    assert_loopback(base_url.rstrip("/") + path)
    return launcher_scheme.request(base_url, path, method=method, body=body, timeout=timeout)


def fetch_issue_state(repo: str, number: int, owner: str = DEFAULT_OWNER) -> Dict[str, Any]:
    """One `gh issue view` per ref (no batched API exists). A `gh` failure
    becomes an `error` field rather than raising, so one bad ref never
    aborts the rest of a multi-repo table."""
    res = git_run.run_gh(
        [
            "issue", "view", str(number),
            "--repo", f"{owner}/{repo}",
            "--json", "number,title,state,labels,updatedAt",
        ],
        stdin=subprocess.DEVNULL,
    )
    if res.returncode != 0:
        return {"repo": repo, "number": number, "error": res.stderr.strip() or "gh error"}
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return {"repo": repo, "number": number, "error": "unparseable gh output"}
    data["repo"] = repo
    return data


def fetch_exchange_marker(base_url: str, sid: str) -> Dict[str, Any]:
    """`{"available": bool, "timestamp": Any}` for `sid`'s current exchange.

    A transport error or an unparseable response both collapse to
    `available: False` — the same "can't tell" outcome `classify_exchange_marker`
    treats as pending, never as delivered. The read-path itself has known
    defects (another session's content, raw frames,
    `capture_unparseable`); this wrapper doesn't try to detect those, it just
    refuses to promote an unreadable read into a delivery signal.
    """
    try:
        result = _request(base_url, f"/api/board/sessions/{sid}/exchange")
    except (urllib.error.URLError, ValueError):
        return {"available": False, "timestamp": None}
    if not result.get("available"):
        return {"available": False, "timestamp": None}
    assistant = result.get("assistant") or {}
    return {"available": True, "timestamp": assistant.get("timestamp")}


def post_session_input(base_url: str, sid: str, text: str) -> Dict[str, Any]:
    """POST one steer into `sid`'s terminal. **Exactly one POST per call** —
    this helper never retries, and a caller may call it again for the same
    steer only after reading the session transcript for that exact text
    (`say_recovery.post_with_recovery`): a resent `/issue-finish` can
    double-execute a merge.

    Returns `{"ok": bool, "reason": str|None, "http_status": int|None,
    "transport": str|None, "error": str|None}`; `transport` is
    `transient_failure`'s verdict for a call that got no usable answer (None
    for a success or an authoritative HTTP answer).

    The session-host answers a *written but never echoed* payload with HTTP
    502, and an already-exited session with 409.
    Both used to escape `urllib` as an unhandled `HTTPError` and crash the
    tool with a traceback — so the one case where the API states outright
    "this was NOT delivered" was the single input the verifier could not
    process, while every verdict it *did* report rested on inference. Caught
    here, those become a `reason` the classifier can act on.

    Success bodies are returned as-is; `reason` (not the HTTP status) is the
    documented contract for `deferred`/202, per app-launcher's
    `src/session_client.py:159-163`.
    """
    try:
        result = _request(
            base_url, f"/api/claude-code/sessions/{sid}/input",
            method="POST", body={"data": text, "submit": True},
        )
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — a body we cannot read must not mask the status
            detail = ""
        # 502 is the host's "written, never echoed, no CR sent" — map it onto
        # the same reason vocabulary the body would have carried, so the
        # classifier has one language for authoritative negatives.
        # A 400/403 is the route refusing the request before it wrote anything: the same
        # "never reached the PTY" fact as a 409, so it shares that reason.
        reason = {502: "not_ingested", 409: "dropped", 400: "dropped", 403: "dropped"}.get(exc.code)
        return {
            "ok": False, "reason": reason, "http_status": exc.code,
            "transport": transient_failure(exc),
            "error": f"HTTP {exc.code}: {detail.strip()[:300] or exc.reason}",
        }
    except (OSError, ValueError) as exc:
        # Could not reach the endpoint, or lost the answer — not evidence either way.
        # `transport` says whether the request can have been handled.
        return {"ok": False, "reason": None, "http_status": None,
                "transport": transient_failure(exc), "error": str(exc)[:300]}
    if not isinstance(result, dict):
        return {"ok": True, "reason": None, "http_status": None, "transport": None, "error": None}
    return {"ok": True, "reason": result.get("reason"), "http_status": None,
            "transport": None, "error": None}


def fetch_session_card(base_url: str, sid: str) -> Dict[str, Any]:
    """The session-host's own record for `sid` from the sessions list, or
    `{}` when it cannot be read. Source of `last_output_at` and — on hosts
    new enough to report `last_input`.

    An older host simply omits `last_input`; that is reported as unknown
    rather than guessed at, which is why the callers below print `unknown`
    instead of inventing a verdict.
    """
    try:
        payload = _request(base_url, "/api/claude-code/sessions")
    except (urllib.error.URLError, ValueError):
        return {}
    sessions = payload.get("sessions") if isinstance(payload, dict) else payload
    for card in sessions or []:
        if isinstance(card, dict) and str(card.get("session_id") or "") == sid:
            return card
    return {}


def _last_input(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """`card`'s `last_input` record, or None when absent or malformed."""
    record = card.get("last_input")
    return record if isinstance(record, dict) else None
