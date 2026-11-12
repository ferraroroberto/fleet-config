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
- the manifest names source types and counts, never a source name;
- the ideas file carries full attribution and a `## Source` section, the ledger writer refuses
  an unknown id and a second outcome each with its own reason, and an id in the ledger is never
  kept again even after it has aged out of `seen.json`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/inspiration_scan/test_inspiration_scan.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as _dt
import io
import json
import os
import shutil
import subprocess
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
import ideas  # noqa: E402
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
check("fleet_core" in scan._start_failure("Traceback (most recent call last):\n  File \"x\"\n"
                                           "ModuleNotFoundError: No module named 'fleet_core'\n", 1)
      and scan._start_failure("", 3).endswith("no output"),
      "scan: a reader that prints no reply is reported with its last stderr line, as its own reason")
# The way scan.py spawns the reader: a bare interpreter whose path holds neither fleet_core nor the
# skill folder (-I drops PYTHONPATH and the script directory), run from an unrelated folder.
_bare_env = {k: v for k, v in os.environ.items() if k.upper() != "PYTHONPATH"}
_spawned = subprocess.run([sys.executable, "-I", str(scan.SKILL_DIR / "x_reader.py"), "read"], input="{}",
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                          cwd=tempfile.gettempdir(), env=_bare_env, creationflags=scan.NO_WINDOW)
check("ModuleNotFoundError" not in _spawned.stderr and _spawned.stdout.strip().startswith("{"),
      "x_reader: spawned under an interpreter without fleet_core on its path, it starts and answers in JSON")
walled = scan._x_results(handles, {"sources": [
    {"handle": "a", "status": "not-read", "reason": "logged-out", "items": []},
    {"handle": "b", "status": "not-read", "reason": "logged-out", "items": []}]})
check([r.status for r in walled] == ["login-needed", "login-needed"],
      "scan: a logged-out X profile is its own login-needed state, not not-read")
challenged = scan._x_results(handles[:1], {"sources": [
    {"handle": "a", "status": "not-read", "reason": "challenge", "items": []}]})
check(challenged[0].status == "not-read", "scan: a challenge page stays not-read")
check("login-needed=2" in scan._manifest(walled)[0] and "ok=0" in scan._manifest(walled)[0],
      "scan: the manifest counts login-needed sources apart from not-read")
walled_page = render.render("2026-11-07", walled, [], [], 0, [])
check("skipped: not logged in (2 of 2)" in walled_page and "not a quiet day on X" in walled_page,
      "render: a skipped X is named as skipped for no login, not reported as an empty source")
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

# ---- ideas file, ledger and writer ----

author_item = feeds.Item("idea1", "newsletter", "Made-up Letter", "A made-up title", "https://letter.example/p/1",
                         " ".join(f"w{n}" for n in range(40)), NOW - _dt.timedelta(days=1), "Pat Example")
author_entry = score.Scored(author_item, 8, "it fits", "try it", "File a made-up idea", "fleet-config", "model-x")
idea = ideas.build_idea(author_entry, "2026-11-07")
check(all(k in idea for k in ("id", "title", "link", "source", "author", "published", "excerpt", "digest", "score",
                              "model", "why_fits", "experiment", "suggested_repo", "prompt"))
      and idea["source"] == {"name": "Made-up Letter", "kind": "newsletter"} and idea["author"] == "Pat Example",
      "ideas: an idea carries every contract field, with the source as name and kind")
check(len(idea["excerpt"].rstrip("…").split()) == ideas.EXCERPT_WORDS and idea["excerpt"].endswith("…"),
      "ideas: the excerpt is cut to the word cap, never the full text")
tail = idea["prompt"].split("## Source", 1)[1] if "## Source" in idea["prompt"] else ""
check(idea["prompt"].startswith("File a made-up idea") and all(
      want in tail for want in ("Pat Example", "2026-11-06", "https://letter.example/p/1", "Digest: 2026-11-07",
                                "8/10", "model-x")) and idea["prompt"].rstrip().endswith("scored by model-x"),
      "ideas: the prompt ends with a Source section naming author, date, link, digest and score/model")
bare = ideas.build_idea(
    score.Scored(feeds.Item("idea2", "x", "@made_up", "t", "https://x.example/1", "short", None), 7), "d")
check("Author: not given" in bare["prompt"] and "Published: not given" in bare["prompt"] and bare["published"] == "",
      "ideas: a missing author or date is stated as not given, never blank")
check(feeds.parse_feed(b"<rss><channel><item><title>t</title><link>https://a.example/1</link>"
                       b"<author>Made Up</author></item></channel></rss>", letter)[0].author == "Made Up"
      and feeds.parse_feed(ATOM.replace(b"<published>", b"<author><name>Chan Nel</name></author><published>"),
                           channel)[0].author == "Chan Nel",
      "feeds: an RSS author or Atom author name reaches the item")

ledger_root = Path(tempfile.mkdtemp(prefix="inspiration-ideas-test-"))


def _writer(*argv: str) -> tuple:
    parser = argparse.ArgumentParser()
    ideas.add_parser(parser.add_subparsers(dest="command", required=True))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = ideas.run_command(parser.parse_args(["ideas", *argv]), ledger_root)
    return code, buf.getvalue()


try:
    ideas.write_ideas(ledger_root, [idea, bare], set(), NOW.date())
    code, said = _writer("apply", "nope", "--issue", "fleet-config#5")
    check(code == 2 and "IDEAS_REFUSED=nope reason=unknown-id" in said and not ideas.ledger_path(ledger_root).exists(),
          "writer: an unknown id is refused and writes nothing")
    code, said = _writer("apply", "idea1", "--issue", "not-a-ref")
    check(code == 2 and "reason=bad-ref" in said and not ideas.ledger_path(ledger_root).exists(),
          "writer: an issue ref that is not <repo>#<n> is refused")
    code, said = _writer("apply", "idea1", "--issue", "fleet-config#5")
    rows = ideas.read_ledger(ledger_root)
    check(code == 0 and len(rows) == 1 and rows[0]["state"] == "applied" and rows[0]["issue"] == "fleet-config#5"
          and rows[0]["at"].endswith("Z") and rows[0]["author"] == "Pat Example" and rows[0]["link"] == idea["link"]
          and rows[0]["source"] == idea["source"] and rows[0]["score"] == 8 and rows[0]["model"] == "model-x",
          "writer: apply appends one applied row with the issue and a copy of the attribution")
    code, said = _writer("filter", "idea1", "--note", "not now")
    check(code == 2 and "reason=already-applied" in said and len(ideas.read_ledger(ledger_root)) == 1,
          "writer: a second outcome for the same id is refused as already-applied and writes nothing")
    code, _said = _writer("filter", "idea2", "--note", "  too vague ")
    rows = ideas.read_ledger(ledger_root)
    check(code == 0 and rows[1]["state"] == "filtered" and rows[1]["note"] == "too vague",
          "writer: filter appends one filtered row with the owner's note")
    code, said = _writer("apply", "idea2", "--issue", "fleet-config#6")
    check(code == 2 and "reason=already-filtered" in said, "writer: a filtered id cannot then be applied")
    # The ledger outlives the ideas file: the refusal reason must still be the true one.
    ideas.write_ideas(ledger_root, [], {"idea1", "idea2"}, NOW.date())
    code, said = _writer("filter", "idea1")
    check(code == 2 and "already-applied" in said, "writer: a decided id stays refused after ideas.json drops it")
    code, said = _writer("list", "--state", "applied")
    check(code == 0 and "state=applied" in said and "IDEAS_ENTRIES=1" in said and "filtered" not in said,
          "writer: list --state applied shows only applied rows")
    ideas.ledger_path(ledger_root).write_text("not json\n", encoding="utf-8")
    code, said = _writer("filter", "idea1")
    check(code == 1 and "IDEAS=unknown" in said, "writer: an unreadable ledger is unknown, never an empty one")
finally:
    shutil.rmtree(ledger_root, ignore_errors=True)

# ---- end to end through cmd_run on a temp state root ----

tmp = Path(tempfile.mkdtemp(prefix="inspiration-scan-test-"))
real_fetch, real_hub = feeds.fetch, score.call_hub
os.environ[sources.STATE_ROOT_ENV_VAR] = str(tmp)
try:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = scan.cmd_run(NOW.date())
    first_digest = tmp / "digests" / f"{NOW.date().isoformat()}.html"
    check(code == scan.EXIT_NO_SOURCES and "STATUS=no-sources" in out.getvalue(),
          "cmd_run: a missing source list is its own exit and status, not an empty digest")
    check((tmp / "sources.toml").is_file() and "SOURCES_FILE=created" in out.getvalue(),
          "cmd_run: a missing source list is created from the example, with no init step")
    check(first_digest.is_file() and "Source list not filled in yet" in first_digest.read_text(encoding="utf-8")
          and str(tmp / "sources.toml") in first_digest.read_text(encoding="utf-8").replace("&#x27;", "'"),
          "cmd_run: the digest says the list is not filled in and names the file")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        again = scan.cmd_run(NOW.date())
    check(again == scan.EXIT_NO_SOURCES and "SOURCES_FILE=unfilled" in out.getvalue(),
          "cmd_run: the untouched example is still unfilled, so its placeholder feeds are never read")

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
    ideas_file = json.loads((tmp / "ideas.json").read_text(encoding="utf-8"))
    kept_ids = [i["id"] for i in ideas_file["ideas"]]
    check(ideas_file["version"] == 1 and len(kept_ids) == 2 and "## Source" in ideas_file["ideas"][0]["prompt"],
          "cmd_run: ideas.json holds the kept ideas, with a version and a Source section in each prompt")
    check("IDEAS=2" in second, "cmd_run: the manifest counts the ideas written")
    third = _run(NOW.date() + _dt.timedelta(days=2))
    check("seen_before=2" in third and "kept=0" in third, "cmd_run: items scored on an earlier day are not shown again")
    check([i["id"] for i in json.loads((tmp / "ideas.json").read_text(encoding="utf-8"))["ideas"]] == kept_ids,
          "cmd_run: ideas the owner has not decided on stay in ideas.json on a later run")
    with contextlib.redirect_stdout(io.StringIO()):
        scan.main(["ideas", "filter", kept_ids[0], "--note", "no"])
    (tmp / "seen.json").write_text("{}", encoding="utf-8")
    fourth = _run(NOW.date() + _dt.timedelta(days=3))
    check("kept=1" in fourth and "seen_before=1" in fourth,
          "cmd_run: an id in the ledger is not kept again even after it aged out of seen.json")
    check([i["id"] for i in json.loads((tmp / "ideas.json").read_text(encoding="utf-8"))["ideas"]] == kept_ids[1:],
          "cmd_run: a decided idea leaves ideas.json")
    digest = tmp / "digests" / f"{(NOW.date() + _dt.timedelta(days=1)).isoformat()}.html"
    check(digest.is_file() and "Fresh &amp; useful" in digest.read_text(encoding="utf-8"),
          "cmd_run: the digest is written under the state root")
finally:
    feeds.fetch, score.call_hub = real_fetch, real_hub
    os.environ.pop(sources.STATE_ROOT_ENV_VAR, None)
    shutil.rmtree(tmp, ignore_errors=True)

# The run date drives the lookback window: the wall clock is read in exactly one place, the CLI entry that
# defaults `--date`. A `.now()`/`.today()` anywhere else makes `run --date` read a window it does not name.
check(scan.window_start(_dt.date(2026, 11, 7), 7) == _dt.datetime(2026, 11, 1, tzinfo=_dt.timezone.utc),
      "scan: the lookback window is the end of the run date's UTC day minus lookback_days")


def _clock_reads(tree: ast.AST) -> dict:
    """function name -> count of `.now()` / `.today()` / `.utcnow()` calls directly inside it."""
    found: dict = {}
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr in ("now", "today", "utcnow")]
        if calls:
            found[fn.name] = len(calls)
    return found


scan_clock = _clock_reads(ast.parse((SKILL / "scan.py").read_text(encoding="utf-8")))
check(scan_clock == {"main": 1}, f"scan: only main() reads the wall clock, to default --date (got {scan_clock})")

_h.report_and_exit("test_inspiration_scan")
