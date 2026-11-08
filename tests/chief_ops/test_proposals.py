"""chief_ops proposals: the chief_weekly_proposals switch, candidate screening, the answers ledger and
the one `kind=proposals` mailbox event, against a fake launcher, fake `gh` reads and a temp inbox
(fleet-config#1896).

No live launcher, no `gh`, no scheduled learning-log run: the launcher transport and both `gh`
reads are replaced, the inbox and every state file live in a throwaway directory.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/chief_ops/test_proposals.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import urllib.error
from datetime import timedelta
from pathlib import Path

from _mailbox_support import run_cli  # noqa: E402
from _suite import _h, check, co  # noqa: E402
from chief import proposals  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="chief_proposals_"))
_INBOX = _TMP / "inbox"
os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(_TMP / "state")
os.environ.pop(co.LAUNCHER_SESSION_ID_ENV_VAR, None)
_LEDGER = _TMP / "state" / proposals.LEDGER_FILENAME
_PUBLIC = ["app-a", "app-b"]
_NOW = proposals._now()


def cand(title="Retry flaky gh reads in the gather step", repo="app-a", type_="enhancement",
         evidence=("app-a#10",), reason="Three PRs this week patched the same transient gh failure."):
    return {"repo": repo, "title": title, "type": type_, "reason": reason, "evidence": list(evidence)}


# ---- the switch: only a literal true is on ---------------------------------------------

check(proposals.proposals_switch({"settings": {"chief_weekly_proposals": True}}) is None, "true is on")
check(proposals.proposals_switch({"settings": {"chief_weekly_proposals": False}})
      == "PROPOSALS=off reason=switch-off", "false is off")
check(proposals.proposals_switch({"settings": {"auto_drain": True}}) == "PROPOSALS=off reason=setting-missing",
      "a launcher without the key (before app-launcher ships it) reads as off")
check((proposals.proposals_switch({"settings": {"chief_weekly_proposals": "true"}}) or "").startswith(
      "PROPOSALS=off reason=setting-not-boolean"), "a non-boolean switch is never on")
check(proposals.proposals_switch(None) == "PROPOSALS=off reason=settings-unreadable", "an unreadable reply is off")


# ---- title overlap and candidate screening -----------------------------------------------

check(proposals.same_idea("Retry flaky gh reads in gather", "Retry flaky gh reads in the gather step"),
      "a reworded title with most tokens shared is the same idea")
check(not proposals.same_idea("Retry flaky gh reads", "Document the ledger archive cap"),
      "unrelated titles are not")
check(proposals.title_overlap("", "anything here") == 0.0, "a title with no tokens overlaps nothing")
check(proposals.week_id(_NOW).startswith(str(_NOW.isocalendar()[0]) + "-W"), "week ids are ISO years and weeks")

for bad, why in ((dict(cand(), repo="life-os"), "repo-not-public"),
                 (dict(cand(), title=" "), "bad-title"),
                 (dict(cand(), type="feature"), "bad-type"),
                 (dict(cand(), reason=""), "bad-reason"),
                 (dict(cand(), reason="x" * 301), "bad-reason"),
                 (dict(cand(), evidence=[]), "no-evidence"),
                 (dict(cand(), evidence=["life-os#4"]), "bad-evidence-ref"),
                 (dict(cand(), evidence=["not a ref"]), "bad-evidence-ref"),
                 ("text", "not-an-object")):
    check(proposals.candidate_problem(bad, _PUBLIC) == why, f"candidate refused as {why}")
check(proposals.candidate_problem(cand(), _PUBLIC) is None, "a well formed public candidate passes")

five = [cand(title=f"Distinct proposal number {w} about {w}ing", evidence=(f"app-a#{i}",))
        for i, w in enumerate(("alpha", "bravo", "charlie", "delta", "echo"), 1)]
kept, dropped = proposals.screen(five, _PUBLIC, [], {}, _NOW)
check(len(kept) == proposals.MAX_CANDIDATES == 4 and dropped == [(five[4]["title"], "over-cap")],
      f"at most four kept, the fifth dropped as over-cap (got {len(kept)}, {dropped})")

kept, dropped = proposals.screen([cand(), cand(title="Retry flaky gh reads in the gather step please")],
                                 _PUBLIC, [], {}, _NOW)
check(len(kept) == 1 and dropped[0][1] == "duplicate-in-batch", "a near-duplicate inside one batch is dropped")

kept, dropped = proposals.screen([cand()], _PUBLIC, [], {"app-a": ["Retry flaky gh reads in gather step"]}, _NOW)
check(not kept and dropped[0][1] == "matches-open-issue", "a candidate matching an open issue's title is dropped")
kept, dropped = proposals.screen([cand()], _PUBLIC, [], {"app-b": ["Retry flaky gh reads in gather step"]}, _NOW)
check(len(kept) == 1, "an open issue in another repo does not block it")


# ---- the ledger shapes the next run: a declined idea needs new evidence ----------------------

def row(answer, title="Retry flaky gh reads in the gather step", evidence=("app-a#10",), age_days=1, **extra):
    return {"pid": "p" + title[:3], "week": "2026-W01", "repo": "app-a", "title": title, "type": "enhancement",
            "evidence": list(evidence), "proposed_at": proposals._iso_z(_NOW - timedelta(days=age_days)),
            "answer": answer, **extra}


declined = [row("declined", note="not worth it")]
kept, dropped = proposals.screen([cand()], _PUBLIC, declined, {}, _NOW)
check(not kept and dropped[0][1] == "declined-no-new-evidence",
      "a declined proposal is not proposed again when its evidence is the same")
kept, dropped = proposals.screen([cand(evidence=("app-a#10", "app-a#55"))], _PUBLIC, declined, {}, _NOW)
check(len(kept) == 1, "the same idea returns when it cites evidence the declined one did not")
kept, dropped = proposals.screen([cand(title="Retry flaky gh reads gather step")], _PUBLIC, declined, {}, _NOW)
check(not kept, "a reworded declined idea is still screened by title overlap")
kept, dropped = proposals.screen([cand(repo="app-b")], _PUBLIC, declined, {}, _NOW)
check(len(kept) == 1, "a declined idea in another repo does not block this repo's")
kept, dropped = proposals.screen([cand()], _PUBLIC, [row("accepted", issue="app-a#99")], {}, _NOW)
check(not kept and dropped[0][1] == "already-accepted", "an accepted proposal is not proposed again")
kept, dropped = proposals.screen([cand()], _PUBLIC, [row("pending", age_days=3)], {}, _NOW)
check(not kept and dropped[0][1] == "already-pending", "a proposal still awaiting an answer is not proposed again")
stale = row("pending", age_days=15)
check(proposals.effective_answer(stale, _NOW) == "unanswered", "no answer in 14 days reads unanswered, not declined")
kept, dropped = proposals.screen([cand()], _PUBLIC, [stale], {}, _NOW)
check(len(kept) == 1, "an unanswered proposal may be offered again")

rates = proposals.accept_rates([row("accepted"), row("declined", title="Other idea entirely here"), stale], _NOW)
check(rates["type"]["enhancement"] == (1, 2) and rates["repo"]["app-a"] == (1, 2),
      f"accept rates count answered proposals only (got {rates})")
check(proposals.last_week_line([], "2026-W02") == "Last week: 0 proposed, 0 filed.", "no history reads 0 and 0")
check(proposals.last_week_line([row("accepted"), row("declined", title="Other idea here"),
                                {**row("accepted", title="Third idea here"), "week": "2026-W02"}], "2026-W02")
      == "Last week: 2 proposed, 1 filed.", "the line counts the newest earlier week, not this one")


# ---- end to end: fake launcher, fake gh, temp inbox --------------------------------------------

class FakeLauncher:
    def __init__(self, switch=True):
        self.switch = switch

    def request(self, base_url, path, method="GET", body=None, timeout=10.0):
        assert path == "/api/board/chief/settings", f"unexpected {method} {path}"
        if isinstance(self.switch, Exception):
            raise self.switch
        return {"settings": {"worker_cap": 3, **({} if self.switch is None else {"chief_weekly_proposals": self.switch})}}


class FakeGh:
    def __init__(self, public=_PUBLIC, titles=None, fail=None):
        self.public, self.titles, self.fail = public, titles or {}, fail
        self.title_reads = []

    def repos(self):
        return (None, "boom") if self.fail == "repos" else (list(self.public), "")

    def open_titles(self, repo):
        self.title_reads.append(repo)
        return (None, "boom") if self.fail == "issues" else (list(self.titles.get(repo, [])), "")


def _run(fake, gh, *argv):
    prior = (co.launcher._request, proposals.fetch_public_repos, proposals.fetch_open_titles,
             co.mailbox.chief_inbox_dir)
    co.launcher._request = fake.request
    proposals.fetch_public_repos, proposals.fetch_open_titles = gh.repos, gh.open_titles
    co.mailbox.chief_inbox_dir = lambda: _INBOX
    try:
        args = co.cli.build_parser("x").parse_args(["proposals", *argv])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            rc = args.func(args)
    finally:
        (co.launcher._request, proposals.fetch_public_repos, proposals.fetch_open_titles,
         co.mailbox.chief_inbox_dir) = prior
    return rc, out.getvalue()


def _reset():
    shutil.rmtree(_TMP / "state", ignore_errors=True)
    shutil.rmtree(_INBOX, ignore_errors=True)


def _file(cands):
    path = _TMP / "candidates.json"
    path.write_text(json.dumps(cands), encoding="utf-8")
    return str(path)


def _ledger():
    return json.loads(_LEDGER.read_text(encoding="utf-8"))["entries"]


def _events():
    return sorted(_INBOX.glob("*.json")) if _INBOX.exists() else []


# Off, missing or unreadable: nothing is read, written or delivered, and the reason is printed.
for switch, reason in ((False, "switch-off"), (None, "setting-missing"),
                       (urllib.error.URLError("down"), "settings-unreadable")):
    _reset()
    gh = FakeGh()
    outs = [_run(FakeLauncher(switch), gh, "context")[1],
            _run(FakeLauncher(switch), gh, "publish", "--file", _file([cand()]))[1]]
    check(all(f"PROPOSALS=off reason={reason}" in o for o in outs) and not _LEDGER.exists() and not _events()
          and not gh.title_reads, f"switch {reason}: no ledger, no event, no gh read (got {outs!r})")

# On: publish writes one event with the screened candidates and records them.
_reset()
gh = FakeGh(titles={"app-a": ["Document the ledger archive cap"]})
cands = [cand(), cand(title="Document the ledger archive cap in the readme", evidence=("app-a#11",)),
         cand(title="Cap the digest at four bullets", repo="app-b", type_="chore", evidence=("app-b#3",)),
         cand(title="Private thing", repo="life-os")]
rc, out = _run(FakeLauncher(), gh, "publish", "--file", _file(cands), "--week", "2020-W45")
event = json.loads(_events()[0].read_text(encoding="utf-8")) if _events() else {}
check(rc == 0 and len(_events()) == 1 and "PROPOSALS_PUBLISHED=2" in out, f"one event, two kept (got {out!r})")
check("PROPOSALS_DROPPED=Document the ledger archive cap in the readme reason=matches-open-issue" in out
      and "PROPOSALS_DROPPED=Private thing reason=repo-not-public" in out, "each drop names its reason")
check(event.get("kind") == "proposals" and event.get("v") == 2 and event.get("week") == "2020-W45"
      and [c["repo"] for c in event["candidates"]] == ["app-a", "app-b"]
      and all(c["reason"] and c["evidence"] and c["pid"] for c in event["candidates"]),
      f"the event carries the kept candidates with reason, evidence and a pid (got {event!r})")
check(not any("life-os" in json.dumps(e) for e in (event, _ledger())) and "life-os" not in gh.title_reads,
      "a private repo's candidate never reaches the event, the ledger or a gh read")
check(len(_ledger()) == 2 and all(e["answer"] == "pending" and e["event_id"] == event["id"] for e in _ledger()),
      "both kept proposals are in the ledger as pending")
check(not list(_INBOX.glob(".*")) and _events()[0].name.endswith(f"-{event['id']}.json"),
      "the event file is named <ms>-<id>.json and no temp file is left")

# A second publish in the same week writes nothing.
rc, out = _run(FakeLauncher(), gh, "publish", "--file", _file(cands), "--week", "2020-W45")
check(rc == 2 and "reason=already-published" in out and len(_events()) == 1 and len(_ledger()) == 2,
      f"one proposals event per week (got {out!r})")

# The real inbox renders it and counts, claims and acks it like any kind.
code, text = run_cli(_INBOX, "inbox")
lines = text.splitlines()
check(code == 0 and any(l.startswith("- weekly proposals n=2 week=2020-W45 kind=proposals id=") for l in lines),
      f"inbox: the head line names the count and week (got {lines[:3]})")
check(any(l.startswith("  [") and "app-a: Retry flaky gh reads in the gather step (enhancement) -- " in l
          for l in lines), "inbox: each candidate renders with repo, title, type and reason")
ids = next(l for l in lines if l.startswith("ACK=")).split()[2:]
code, text = run_cli(_INBOX, "ack", *ids)
check(code == 0 and "ACKED=1" in text and not _events(), "ack retires the proposals event")

# Answers: a decline carries its note, an accept carries the issue; the next run reads them.
pid_a, pid_b = (e["pid"] for e in _ledger())
rc, out = _run(FakeLauncher(), gh, "answer", pid_a[:8], "--declined", "--note", "too small")
check(rc == 0 and "PROPOSAL_ANSWERED=" in out and "answer=declined" in out, f"a decline is recorded (got {out!r})")
rc, out = _run(FakeLauncher(), gh, "answer", pid_b, "--accepted", "--issue", "app-b#40")
check(rc == 0 and _ledger()[1]["answer"] == "accepted" and _ledger()[1]["issue"] == "app-b#40",
      "an accept records the filed issue")
rc, out = _run(FakeLauncher(), gh, "answer", pid_b, "--accepted", "--issue", "nonsense")
check(rc == 2 and "reason=bad-ref" in out, "a malformed issue ref is refused")
rc, out = _run(FakeLauncher(), gh, "answer", "zzzz", "--accepted")
check(rc == 2 and "reason=no-single-match" in out, "an unknown proposal id is refused")

rc, out = _run(FakeLauncher(), gh, "context")
check("PROPOSALS=on" in out and "PROPOSALS_LAST_WEEK=Last week: 2 proposed, 1 filed." in out
      and f"PROPOSALS_DECLINED=app-a | {cands[0]['title']} | evidence=app-a#10 note=too small" in out
      and "PROPOSALS_RATE=type:enhancement accepted=0/1" in out and "PROPOSALS_RATE=type:chore accepted=1/1" in out,
      f"context hands the next run last week's line, the declined list and the rates (got {out!r})")

# Next week: the declined idea is not proposed again without new evidence, and is with it.
gh = FakeGh()
rc, out = _run(FakeLauncher(), gh, "publish", "--file", _file([cand()]), "--week", "2099-W01")
check("PROPOSALS_DROPPED=" in out and "reason=declined-no-new-evidence" in out and "PROPOSALS_PUBLISHED=0" in out
      and not _events(), f"a declined item is not published again without new evidence (got {out!r})")
rc, out = _run(FakeLauncher(), gh, "publish", "--file", _file([cand(evidence=("app-a#10", "app-a#70"))]),
               "--week", "2099-W02")
check("PROPOSALS_PUBLISHED=1" in out and len(_events()) == 1, "new evidence lets the idea return")

# A fact that cannot be established is unknown, never a pass.
for fail, why in (("repos", "public-repos"), ("issues", "open-issues")):
    _reset()
    rc, out = _run(FakeLauncher(), FakeGh(fail=fail), "publish", "--file", _file([cand()]))
    check(rc == 1 and f"PROPOSALS=unknown reason={why}" in out and not _events() and not _LEDGER.exists(),
          f"an unreadable {fail} read is unknown and writes nothing (got {out!r})")
_reset()
_LEDGER.parent.mkdir(parents=True)
_LEDGER.write_text("{not json", encoding="utf-8")
rc, out = _run(FakeLauncher(), FakeGh(), "publish", "--file", _file([cand()]))
check(rc == 1 and "PROPOSALS=unknown reason=ledger-or-inbox" in out and not _events(),
      "an unreadable ledger is unknown, and no event goes out without it")
rc, out = _run(FakeLauncher(), FakeGh(), "publish", "--file", str(_TMP / "missing.json"))
check(rc == 1 and "reason=candidates-unreadable" in out, "an unreadable candidates file is unknown")

shutil.rmtree(_TMP, ignore_errors=True)
_h.report_and_exit("test_proposals")
