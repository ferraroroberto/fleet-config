"""Read-only X timeline reader for /inspiration-scan, driven through a real Chrome.

Runs under a Python that has Playwright (app-launcher's `.venv`), spawned by
`scan.py`, because fleet-config's own runtime is stdlib-only. It only opens
profile pages and reads what renders: it never posts, likes, follows or clicks
anything. The X API is paid, so this reads the logged-in web UI instead.

  read   stdin: {"handles": [...], "profile_dir": "...", "since": ISO, "scrolls": N}
         stdout: {"sources": [{"handle", "status", "reason", "items": [...]}]}
  login  --profile-dir DIR   opens the dedicated profile headed at the X login
         page and waits until the window is closed (one-time owner step).

A login wall or a challenge page stops the pass: every handle not yet read is
reported `not-read` with that reason, so an expired session is never mistaken
for a quiet day.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# The Playwright Python this runs under has neither this folder nor fleet-config's
# shared package on its path, and `x_session` imports `fleet_core`.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import x_session  # noqa: E402

X_HOME = "https://x.com"
# Long enough for a profile page to render its first posts on a slow night.
RENDER_TIMEOUT_MS = 15000
# A pause between accounts in the range a person clicking through would take.
PACE_RANGE_S = (4.0, 9.0)
SCROLL_PAUSE_S = 2.0
# Rendered only for a signed-in session; X serves logged-out profile pages
# without redirecting to the login flow, so the URL alone cannot tell.
SIGNED_IN_SELECTOR = '[data-testid="SideNav_AccountSwitcher_Button"]'

_EXTRACT_JS = """(handle) => {
  const out = [];
  for (const art of document.querySelectorAll('article[data-testid="tweet"]')) {
    const time = art.querySelector('time[datetime]');
    const anchor = time ? time.closest('a[href*="/status/"]') : null;
    if (!anchor) continue;
    const href = anchor.getAttribute('href') || '';
    if (!href.toLowerCase().startsWith('/' + handle.toLowerCase() + '/status/')) continue;
    const body = art.querySelector('div[data-testid="tweetText"]');
    out.push({href, datetime: time.getAttribute('datetime'), text: body ? body.innerText : ''});
  }
  return out;
}"""


def classify_page(url: str, page_text: str, signed_in: bool) -> Optional[str]:
    """A reason the pass cannot read this page, or None when posts can be read."""
    lowered = url.lower()
    if "/account/access" in lowered or "captcha" in lowered or "arkose" in page_text.lower():
        return "challenge"
    if "/login" in lowered or "/i/flow/" in lowered or not signed_in:
        return "logged-out"
    return None


def posts_from_raw(raw: List[Dict[str, Any]], since: _dt.datetime) -> List[Dict[str, Any]]:
    """Dedupe extracted posts and keep those at or after `since`."""
    seen, posts = set(), []
    for entry in raw:
        href = entry.get("href") or ""
        if not href or href in seen:
            continue
        seen.add(href)
        try:
            published = _dt.datetime.fromisoformat(str(entry.get("datetime", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if published < since:
            continue
        posts.append({"link": X_HOME + href.split("?", 1)[0], "text": str(entry.get("text", "")),
                      "published": published.isoformat()})
    return posts


def _read_handle(page: Any, handle: str, since: _dt.datetime, scrolls: int) -> Dict[str, Any]:
    page.goto(f"{X_HOME}/{handle}", wait_until="domcontentloaded")
    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=RENDER_TIMEOUT_MS)
    except Exception as exc:  # Playwright's TimeoutError; the page state below says why
        print(f"{handle}: no post rendered within {RENDER_TIMEOUT_MS} ms ({type(exc).__name__})", file=sys.stderr)
    blocked = classify_page(page.url, page.inner_text("body"), page.query_selector(SIGNED_IN_SELECTOR) is not None)
    if blocked:
        return {"handle": handle, "status": "not-read", "reason": blocked, "items": []}
    raw: List[Dict[str, Any]] = []
    for _ in range(scrolls):
        raw.extend(page.evaluate(_EXTRACT_JS, handle))
        page.mouse.wheel(0, 2400)
        time.sleep(SCROLL_PAUSE_S)
    raw.extend(page.evaluate(_EXTRACT_JS, handle))
    if not raw:
        return {"handle": handle, "status": "not-read", "reason": "no posts rendered", "items": []}
    return {"handle": handle, "status": "ok", "reason": "", "items": posts_from_raw(raw, since)}


def read_all(request: Dict[str, Any]) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright

    handles = [str(h) for h in request.get("handles", [])]
    since = _dt.datetime.fromisoformat(str(request["since"]))
    scrolls = int(request.get("scrolls", 3))
    results: List[Dict[str, Any]] = []
    with sync_playwright() as pw:
        context = x_session.open_context(pw, Path(request["profile_dir"]))
        try:
            page = context.pages[0] if context.pages else context.new_page()
            for index, handle in enumerate(handles):
                result = _read_handle(page, handle, since, scrolls)
                results.append(result)
                if result["reason"] in {"logged-out", "challenge"}:
                    results.extend({"handle": h, "status": "not-read", "reason": result["reason"], "items": []}
                                   for h in handles[index + 1:])
                    break
                time.sleep(random.uniform(*PACE_RANGE_S))  # noqa: S311 - pacing jitter, not security
        finally:
            context.close()
    return {"sources": results}


def login(profile_dir: Path) -> int:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = x_session.open_context(pw, profile_dir)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(f"{X_HOME}/login")
        print("Log into X in the Chrome window, then close the window.", flush=True)
        context.wait_for_event("close", timeout=0)
    print("Profile saved.", flush=True)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only X timeline reader for /inspiration-scan.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("read", help="read handles named on stdin as JSON")
    login_parser = sub.add_parser("login", help="log into X once in the dedicated profile")
    login_parser.add_argument("--profile-dir", required=True)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "login":
        return login(Path(args.profile_dir))
    try:
        result = read_all(json.loads(sys.stdin.read() or "{}"))
    except x_session.ProfileLocked as exc:
        result = {"error": "profile-locked", "detail": str(exc)}
    except Exception as exc:  # any browser failure reaches scan.py as one named state
        result = {"error": "browser-failed", "detail": f"{type(exc).__name__}: {exc}"[:300]}
    print(json.dumps(result))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
