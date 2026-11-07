"""Tests for /inspiration-scan's deterministic helpers.

Pins the properties the daily run depends on, against recorded feed snippets, a
stubbed hub and a temp state root -- no network, no browser:

- every source ends `ok`, `not-read` with a reason, or `unknown`, never a
  silent "nothing new";
- items seen on earlier days are dropped, and a batch the hub did not score is
  shown as unscored and NOT marked seen, so it comes back the next day;
- the X launch helper carries the fleet's stealth profile and waits on a held
  profile instead of failing or killing it;
- the HTML digest escapes feed text and carries no script;
- the manifest names source types and counts, never a source name.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/inspiration_scan/test_inspiration_scan.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SKILL = REPO / ".claude" / "skills" / "inspiration-scan"

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

sys.path.insert(0, str(SKILL))
import feeds  # noqa: E402
import render  # noqa: E402
import scan  # noqa: E402
import score  # noqa: E402
import sources  # noqa: E402
import x_reader  # noqa: E402
import x_session  # noqa: E402

_h = CheckHarness()
check = _h.check

NOW = _dt.datetime(2026, 11, 7, 12, 0, tzinfo=_dt.timezone.utc)
SINCE = NOW - _dt.timedelta(days=7)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Letter</title>
<item><title>Fresh &amp; useful</title><link>https://letter.example/p/fresh?utm_source=x</link>
<description>&lt;p&gt;Agents that &lt;b&gt;test&lt;/b&gt; themselves&lt;/p&gt;</description>
<pubDate>Fri, 06 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>Old</title><link>https://letter.example/p/old</link><pubDate>Mon, 01 Jan 2024 08:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<entry><title>Video one</title><link rel="alternate" href="https://www.youtube.com/watch?v=abc"/>
<published>2026-11-05T10:00:00+00:00</published>
<media:group><media:description>A walk through a &lt;script&gt;x&lt;/script&gt; build</media:description></media:group></entry>
</feed>"""


def _src(kind: str, name: str, url: str) -> sources.Source:
    return sources.Source(kind, name, url)


# ---- source list ----

example = sources.load_config(SKILL / "sources.example.toml")
check([s.kind for s in example.sources] == ["newsletter", "youtube", "x"],
      "the committed example parses into one source of each type")
check(example.sources[1].url.endswith("channel_id=UCxxxxxxxxxxxxxxxxxxxxxx"),
      "a YouTube channel id becomes its public feed URL")
for bad, why in (({"newsletter": [{"name": "n"}]}, "a newsletter without a feed"),
                 ({"settings": {"top_n": 0}}, "a non-positive top_n"),
                 ({"x": "handle"}, "[[x]] that is not a list of tables")):
    try:
        sources.parse_config(bad, REPO)
        check(False, f"parse_config rejects {why}")
    except sources.ConfigError:
        pass

# ---- feeds ----

letter = _src("newsletter", "Letter", "https://letter.example/feed")
rss = feeds.read_source(letter, SINCE, fetcher=lambda url: RSS)
check(rss.status == "ok" and [i.title for i in rss.items] == ["Fresh & useful"],
      "RSS: items inside the lookback are kept and older ones dropped")
check(rss.items and rss.items[0].text == "Agents that test themselves",
      "RSS: description HTML is reduced to plain text")
check(feeds.item_id("https://a.example/p?utm_source=x#top") == feeds.item_id("https://a.example/p"),
      "item ids ignore utm parameters and fragments, so a re-shared link dedupes")

channel = _src("youtube", "Channel", "https://www.youtube.com/feeds/videos.xml?channel_id=UC1")
atom = feeds.read_source(channel, SINCE, fetcher=lambda url: ATOM)
check(atom.status == "ok" and atom.items and atom.items[0].link == "https://www.youtube.com/watch?v=abc",
      "Atom: a YouTube entry yields its watch link")
check(atom.items and "build" in atom.items[0].text, "Atom: the media description is the item text")


def _http_error(url: str) -> bytes:
    raise urllib.error.HTTPError(url, 404, "nf", {}, None)


def _url_error(url: str) -> bytes:
    raise urllib.error.URLError("down")


for fetcher, want, label in ((_http_error, "HTTP 404", "an HTTP error"),
                             (_url_error, "fetch failed: URLError", "an unreachable host"),
                             (lambda url: b"<html>nope</html>", "not a readable RSS/Atom feed", "a non-feed page")):
    result = feeds.read_source(letter, SINCE, fetcher=fetcher)
    check(result.status == "not-read" and result.reason == want and not result.items,
          f"feeds: {label} is not-read with its reason, never an empty ok")
check(feeds.read_source(_src("newsletter", "n", "file:///etc/passwd"), SINCE).status == "not-read",
      "feeds: a non-http(s) URL is never fetched")

# ---- scoring ----

items = rss.items + atom.items
reply = json.dumps([{"id": items[0].id, "score": 14, "why_fits": "w", "experiment": "e", "issue_idea": "i"}])
scored, errors = score.score_items(items, "profile", "likes", "m", caller=lambda prompt, model: "ok " + reply)
check(not errors and scored[0].score == 10, "scoring: a score is clamped to 0-10 and prose around the JSON ignored")
check(scored[1].score is None, "scoring: an item the reply leaves out is unscored, not zero")


def _hub_down(prompt: str, model: str) -> str:
    raise urllib.error.URLError("refused")


scored_down, errors_down = score.score_items(items, "p", "l", "m", caller=_hub_down)
check(all(s.score is None for s in scored_down) and errors_down == ["batch 1: URLError"],
      "scoring: an unreachable hub leaves every item unscored and names the failed batch")
prompt = score.build_prompt(items, "", "")
check("(no repo descriptions found)" in prompt and "(not given)" in prompt,
      "scoring: an empty fleet profile or likes note is stated, not silently blank")

# ---- X reader + launch helper ----

kwargs = x_session.launch_kwargs()
check(kwargs["channel"] == "chrome" and kwargs["chromium_sandbox"] is True
      and kwargs["viewport"] == {"width": 1280, "height": 900}
      and "--enable-automation" in kwargs["ignore_default_args"]
      and "--disable-blink-features=AutomationControlled" in kwargs["args"],
      "x_session: the launch carries the fleet's stealth profile")
check("webdriver" in x_session.WEBDRIVER_INIT_SCRIPT and "undefined" in x_session.WEBDRIVER_INIT_SCRIPT,
      "x_session: navigator.webdriver is hidden by an init script")

waits: list = []
attempts = {"n": 0}


def _locked_twice() -> str:
    attempts["n"] += 1
    if attempts["n"] <= 2:
        raise RuntimeError("Opening in existing browser session.")
    return "context"


check(x_session.launch_with_backoff(_locked_twice, sleep=waits.append) == "context" and waits == [60, 120],
      "x_session: a held profile is waited on with the backoff schedule, then opened")
try:
    x_session.launch_with_backoff(lambda: (_ for _ in ()).throw(RuntimeError("ProcessSingleton held")),
                                  schedule=(1, 2), sleep=lambda s: None)
    check(False, "x_session: a profile held through the schedule raises ProfileLocked")
except x_session.ProfileLocked:
    pass
try:
    x_session.launch_with_backoff(lambda: (_ for _ in ()).throw(RuntimeError("chrome not installed")),
                                  sleep=lambda s: check(False, "x_session: a non-lock error is not retried"))
    check(False, "x_session: a non-lock error propagates")
except RuntimeError as exc:
    check("not installed" in str(exc), "x_session: a non-lock error propagates unchanged")

check(x_reader.classify_page("https://x.com/i/flow/login?redirect=1", "", True) == "logged-out",
      "x_reader: the login flow is logged-out")
check(x_reader.classify_page("https://x.com/someone", "posts", False) == "logged-out",
      "x_reader: a profile page served without a signed-in session is logged-out, not a quiet account")
check(x_reader.classify_page("https://x.com/account/access", "", False) == "challenge",
      "x_reader: the access check is a challenge")
check(x_reader.classify_page("https://x.com/someone", "posts", True) is None, "x_reader: a profile page is readable")
raw = [{"href": "/someone/status/1?s=20", "datetime": "2026-11-06T09:00:00.000Z", "text": "new"},
       {"href": "/someone/status/1?s=20", "datetime": "2026-11-06T09:00:00.000Z", "text": "new"},
       {"href": "/someone/status/2", "datetime": "2025-01-01T09:00:00.000Z", "text": "old"}]
posts = x_reader.posts_from_raw(raw, SINCE)
check([p["link"] for p in posts] == ["https://x.com/someone/status/1"],
      "x_reader: posts are deduped, query-stripped and held to the lookback")

handles = [_src("x", "@a", "a"), _src("x", "@b", "b")]
missing = scan.read_x(handles, sources.Settings(x_python=str(REPO / "no-such-python.exe")), SINCE)
check([r.status for r in missing] == ["unknown", "unknown"] and "no Playwright" in missing[0].reason,
      "scan: an absent Playwright Python makes every X source unknown, not empty")
mapped = scan._x_results(handles, {"sources": [
    {"handle": "a", "status": "ok", "items": [{"link": "https://x.com/a/status/9", "text": "hi",
                                                "published": "2026-11-06T09:00:00+00:00"}]}]})
check(mapped[0].status == "ok" and mapped[0].items[0].kind == "x" and mapped[1].status == "unknown",
      "scan: a handle the reader returned nothing for is unknown")

# ---- dedupe + rank ----

seen = {items[0].id: "2026-11-01"}
new, dropped = scan.select_new([rss, atom], seen)
check(dropped == 1 and [i.id for i in new] == [items[1].id], "scan: an item seen on an earlier day is dropped")
check(scan.prune_seen({"a": "2026-01-01", "b": "2026-11-01"}, NOW.date()) == {"b": "2026-11-01"},
      "scan: seen ids past the retention window are pruned")
ranked = [score.Scored(items[0], 9), score.Scored(items[1], 3), score.Scored(items[1], None)]
top, unscored = scan.rank(ranked, top_n=5)
check([s.score for s in top] == [9] and len(unscored) == 1,
      "scan: items under the score bar are dropped and unscored ones kept apart")

# ---- render ----

evil = feeds.Item("e1", "newsletter", "Letter", "<script>alert(1)</script>", "https://e.example/\"x", "t", None)
page = render.render("2026-11-07", [rss, feeds.SourceResult(letter, "not-read", "HTTP 404")],
                     [score.Scored(evil, 8, "fits", "try", "idea")], [], 2, [])
check("<script" not in page and "&lt;script&gt;" in page, "render: feed text is escaped and the page has no script")
check("Newsletters 1/2 read" in page and "not-read: HTTP 404" in page,
      "render: the health chip counts read sources and names why the rest were not")
check("/issue-add idea" in page and "2 item(s) already seen" in page,
      "render: each card has its /issue-add line and the footer counts seen items")

# ---- end to end through cmd_run on a temp state root ----

tmp = Path(tempfile.mkdtemp(prefix="inspiration-scan-test-"))
real_fetch, real_hub = feeds.fetch, score.call_hub
os.environ[sources.STATE_ROOT_ENV_VAR] = str(tmp)
try:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = scan.cmd_run(NOW.date())
    check(code == scan.EXIT_NO_SOURCES and "STATUS=no-sources" in out.getvalue(),
          "cmd_run: a missing source list is its own exit and status, not an empty digest")

    (tmp / "sources.toml").write_text(
        '[[newsletter]]\nname = "Private Letter Name"\nfeed = "https://letter.example/feed"\n'
        '[[youtube]]\nname = "Private Channel"\nchannel_id = "UC1"\n', encoding="utf-8")
    feeds.fetch = lambda url: RSS if "letter" in url else ATOM
    calls = {"n": 0}

    def _hub_once_down(prompt: str, model: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("refused")
        rows = [{"id": i, "score": 8, "why_fits": "w", "experiment": "e", "issue_idea": "i"}
                for i in (it.id for it in items)]
        return json.dumps(rows)

    score.call_hub = _hub_once_down

    def _run(day: _dt.date) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            scan.cmd_run(day)
        return buf.getvalue()

    first = _run(NOW.date())
    check("STATUS=degraded" in first and "unscored=2" in first and "SCORING_ERROR=batch 1: URLError" in first,
          "cmd_run: a hub outage is a degraded run with every item unscored")
    check("Private" not in first, "cmd_run: the manifest never names a source")
    check(json.loads((tmp / "seen.json").read_text(encoding="utf-8")) == {},
          "cmd_run: unscored items are not marked seen")
    second = _run(NOW.date() + _dt.timedelta(days=1))
    check("kept=2" in second and "STATUS=ok" in second, "cmd_run: the next day scores the items the hub missed")
    third = _run(NOW.date() + _dt.timedelta(days=2))
    check("seen_before=2" in third and "kept=0" in third, "cmd_run: items scored on an earlier day are not shown again")
    digest = tmp / "digests" / f"{(NOW.date() + _dt.timedelta(days=1)).isoformat()}.html"
    check(digest.is_file() and "Fresh &amp; useful" in digest.read_text(encoding="utf-8"),
          "cmd_run: the digest is written under the state root")
finally:
    feeds.fetch, score.call_hub = real_fetch, real_hub
    os.environ.pop(sources.STATE_ROOT_ENV_VAR, None)
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_inspiration_scan")
