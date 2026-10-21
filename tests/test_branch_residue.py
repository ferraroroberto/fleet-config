"""Tests for skills/_lib/branch_residue.py (fleet-config#1516).

The teardown branch report decides which leftover branches are *provably*
landed. The rule is positive proof only: the linked issue is closed AND the tip
is proven merged AND no open PR has the head. These checks pin that rule and,
above all, the ways it must refuse:

  * landed -- a squash-merged tip and an ancestor tip, local and remote, with
    the proof in the reason;
  * never landed -- closed issue but unmerged work, a squash later edited on the
    same lines (conservative), an open issue (a fresh empty branch is trivially
    an ancestor), an open PR, a checked-out branch, the default branch, a name
    with no issue number;
  * unknown, its own state -- a PR number, a failed issue lookup, a failed PR
    lookup, a remote tip whose object is not local, a failed `ls-remote`;
  * report-only -- no ref and no folder changes by running the report;
  * orphan `<repo>-wt-*` folders: unregistered ones reported, registered ones
    and look-alike names ignored.

Real throwaway git repos (bare origin + clone), `gh` lookups injected, nothing
outside a temp dir and no live repo's branches read or written.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_branch_residue.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import branch_residue as br  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- pure core ----------------------------------------------------------

def cr(**over):
    base = dict(is_default=False, checked_out=False, issue=7, issue_state="closed", tip_readable=True,
                merged=True, merged_how="ancestor of origin/main", open_pr=False)
    base.update(over)
    return br.classify_ref(**base)[0]


check(br.branch_issue("fix/28-terminal-reconnect") == 28 and br.branch_issue("feat/9") == 9,
      "branch_issue: <type>/<N>-slug and <type>/<N> carry the number")
check(br.branch_issue("main") is None and br.branch_issue("housekeeping-2026") is None
      and br.branch_issue("feat/no-number") is None and br.branch_issue("feat/12x-thing") is None,
      "branch_issue: names without a leading issue number have none")

check(cr() == br.LANDED, "classify: closed issue + proven merged + no open PR is landed")
check(cr(is_default=True) == br.DEFAULT and cr(checked_out=True) == br.CHECKED_OUT,
      "classify: default and checked-out are decided before anything else")
check(cr(is_default=True, checked_out=True, issue=None) == br.DEFAULT, "classify: default outranks checked-out")
check(cr(issue=None, issue_state=None) == br.NO_ISSUE, "classify: no issue number is no-issue, never landed")
check(cr(issue_state="open") == br.OPEN_ISSUE, "classify: an open issue is never landed even when the tip is merged")
check(cr(issue_state=None) == br.UNKNOWN, "classify: an unestablished issue state is unknown")
check(cr(tip_readable=False, merged=None) == br.UNKNOWN, "classify: an unreadable tip is unknown")
check(cr(merged=None) == br.UNKNOWN, "classify: an unestablished merge proof is unknown")
check(cr(merged=False) == br.UNLANDED, "classify: closed issue but unmerged is unlanded")
check(cr(open_pr=None) == br.UNKNOWN, "classify: an unestablished PR lookup is unknown, not landed")
check(cr(open_pr=True) == br.OPEN_PR, "classify: an open PR on a merged tip is open-pr")

rows = [br.Row(br.Ref("a", "local", "0" * 40), 1, c, "") for c in
        (br.LANDED, br.LANDED, br.UNLANDED, br.UNKNOWN, br.DEFAULT, br.CHECKED_OUT, br.NO_ISSUE, br.OPEN_ISSUE, br.OPEN_PR)]
check(br.summarize(rows) == {"landed": 2, "unlanded": 1, "keep": 5, "unknown": 1},
      "summarize: unknown is counted on its own, never folded into landed or keep")

check(br.classify_orphan(True, True)[0] == "orphan-with-git-file"
      and br.classify_orphan(False, True)[0] == "orphan-has-content"
      and br.classify_orphan(False, False)[0] == "orphan-empty"
      and br.classify_orphan(False, None)[0] == br.UNKNOWN,
      "classify_orphan: .git entry, content, empty, and unreadable are four distinct classes")


# ---- real git fixtures --------------------------------------------------

_no_hooks = Path(tempfile.mkdtemp(prefix="br-nohooks-"))


def git(cwd: Path, *args: str) -> str:
    # `core.hooksPath` -> an empty dir: this machine carries a global commit
    # hook that rejects non-allowlisted author emails.
    res = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", f"core.hooksPath={_no_hooks}", *args],
        cwd=str(cwd), capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    if res.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def commit(cwd: Path, name: str, text: str, msg: str) -> None:
    (cwd / name).write_text(text, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", msg)


base = Path(tempfile.mkdtemp(prefix="br-fixture-")).resolve()
origin = base / "origin.git"
git(base, "init", "-q", "--bare", "-b", "main", str(origin))
primary = base / "alpha"
git(base, "clone", "-q", str(origin), str(primary))
git(primary, "checkout", "-q", "-b", "main")
commit(primary, "a.txt", "one\n", "base")
git(primary, "push", "-q", "-u", "origin", "main")
git(primary, "remote", "set-head", "origin", "main")


def squash(name: str, filename: str) -> None:
    """A branch with one commit, pushed, then the same content landed on main as a separate commit."""
    git(primary, "checkout", "-q", "-b", name, "main")
    commit(primary, filename, f"{name}\n", f"work on {name}")
    git(primary, "push", "-q", "origin", name)
    git(primary, "checkout", "-q", "main")
    commit(primary, filename, f"{name}\n", f"squash of {name}")
    git(primary, "push", "-q", "origin", "main")


# Cut before main moves: an ancestor, and a fresh branch for a still-open issue.
git(primary, "branch", "feat/11-ancestor", "main")
git(primary, "branch", "feat/14-fresh", "main")
git(primary, "push", "-q", "origin", "feat/11-ancestor", "feat/14-fresh")

squash("feat/10-squashed", "s10.txt")
squash("feat/15-openpr", "s15.txt")
squash("feat/16-checkedout", "s16.txt")
squash("feat/17-pr-number", "s17.txt")
squash("feat/19-ahead", "s19.txt")

# Closed issue, work never merged.
git(primary, "checkout", "-q", "-b", "feat/12-unmerged", "main")
commit(primary, "u12.txt", "unshipped\n", "unshipped work")
git(primary, "push", "-q", "origin", "feat/12-unmerged")
git(primary, "checkout", "-q", "main")

# Closed issue, edited on the same line main later changed: not provable.
git(primary, "checkout", "-q", "-b", "feat/13-conflict", "main")
commit(primary, "a.txt", "branch edit\n", "edit a")
git(primary, "push", "-q", "origin", "feat/13-conflict")
git(primary, "checkout", "-q", "main")
commit(primary, "a.txt", "main edit\n", "later edit of the same line")
git(primary, "push", "-q", "origin", "main")

# The local tip is ahead of the landed remote tip.
git(primary, "checkout", "-q", "feat/19-ahead")
commit(primary, "extra19.txt", "local only\n", "local extra")
git(primary, "checkout", "-q", "main")

git(primary, "branch", "housekeeping-2026", "main")  # local only, no issue number
git(primary, "fetch", "-q", "origin")

wt16 = base / "alpha-wt-16"
git(primary, "worktree", "add", "-q", str(wt16), "feat/16-checkedout")

ISSUES = {10: "closed", 11: "closed", 12: "closed", 13: "closed", 14: "open", 15: "closed", 16: "closed",
          18: "closed", 19: "closed"}  # 17 is a PR number: the lookup yields None
lookups: list = []


def issue_state(n: int):
    lookups.append(n)
    return ISSUES.get(n)


def report(prs=lambda: {"feat/15-openpr"}, fetch=False, repo=primary):
    lookups.clear()
    return br.build_report(repo, fetch=fetch, issue_state_fn=issue_state, open_pr_heads_fn=prs)


def table(rep):
    return {(r.ref.name, r.ref.where): (r.cls, r.reason) for r in rep.rows}


def cls_of(t, name, where="local"):
    return t.get((name, where), ("<missing>", ""))[0]


def snapshot() -> tuple:
    return (git(primary, "for-each-ref", "refs/"), git(primary, "ls-remote", "--heads", "origin"),
            git(primary, "worktree", "list", "--porcelain"), sorted(p.name for p in base.iterdir()))


before = snapshot()
rep = report()
t = table(rep)

check(cls_of(t, "feat/10-squashed") == br.LANDED and cls_of(t, "feat/10-squashed", "remote") == br.LANDED,
      f"landed: a squash-merged branch with a closed issue is landed, local and remote (got {t.get(('feat/10-squashed', 'local'))})")
check("squash-merged" in t[("feat/10-squashed", "local")][1] and "issue #10 closed" in t[("feat/10-squashed", "local")][1],
      "landed: the reason carries the proof and the issue")
check(cls_of(t, "feat/11-ancestor") == br.LANDED and "ancestor" in t[("feat/11-ancestor", "local")][1],
      "landed: an ancestor tip with a closed issue is landed via the ancestor proof")
check(cls_of(t, "feat/12-unmerged") == br.UNLANDED and cls_of(t, "feat/12-unmerged", "remote") == br.UNLANDED,
      "unlanded: closed issue but unmerged work is never landed")
check(cls_of(t, "feat/13-conflict") == br.UNLANDED,
      "unlanded: a branch conflicting with later main is not proven landed -- conservative")
check(cls_of(t, "feat/14-fresh") == br.OPEN_ISSUE and cls_of(t, "feat/14-fresh", "remote") == br.OPEN_ISSUE,
      "open-issue: a fresh branch (trivially an ancestor) for an open issue is never landed")
check(cls_of(t, "feat/15-openpr") == br.OPEN_PR and cls_of(t, "feat/15-openpr", "remote") == br.OPEN_PR,
      "open-pr: a landed-looking branch with an open PR is not landed")
check(cls_of(t, "feat/16-checkedout") == br.CHECKED_OUT and cls_of(t, "feat/16-checkedout", "remote") == br.CHECKED_OUT,
      "checked-out: a branch checked out in a linked worktree is left alone, local and remote")
check(16 not in lookups, "checked-out: no issue lookup is spent on a ref decided before it")
check(cls_of(t, "main") == br.DEFAULT and cls_of(t, "main", "remote") == br.DEFAULT, "default: main is never touched")
check(cls_of(t, "housekeeping-2026") == br.NO_ISSUE, "no-issue: a name without an issue number is never landed")
check(cls_of(t, "feat/17-pr-number") == br.UNKNOWN and cls_of(t, "feat/17-pr-number", "remote") == br.UNKNOWN,
      "unknown: an issue number the lookup cannot resolve (a PR number) is unknown, never landed")
check(cls_of(t, "feat/19-ahead") == br.UNLANDED and cls_of(t, "feat/19-ahead", "remote") == br.LANDED,
      "per-tip: a local branch ahead of its landed remote tip is judged separately")
check(rep.verdict == br.VERDICT_COMPLETE and not rep.reason, f"report: a full read is complete (got {rep.verdict})")
check(snapshot() == before, "report-only: refs, remote refs, worktrees and sibling folders are byte-identical after the report")

# The PR lookup failing must not read as "no open PR".
t_nopr = table(report(prs=lambda: None))
check(cls_of(t_nopr, "feat/10-squashed") == br.UNKNOWN and cls_of(t_nopr, "feat/10-squashed", "remote") == br.UNKNOWN,
      "unknown: a failed PR lookup turns landed into unknown, not landed")
check(cls_of(t_nopr, "feat/12-unmerged") == br.UNLANDED, "unknown: a failed PR lookup does not disturb refs it never gates")

# A remote tip the primary never fetched cannot be proven.
beta = base / "beta"
git(base, "clone", "-q", str(origin), str(beta))
git(beta, "checkout", "-q", "-b", "fix/18-remote-only", "origin/main")
commit(beta, "r18.txt", "remote\n", "remote-only work")
git(beta, "push", "-q", "origin", "fix/18-remote-only")
t_unfetched = table(report(fetch=False))
check(cls_of(t_unfetched, "fix/18-remote-only", "remote") == br.UNKNOWN
      and "not present locally" in t_unfetched[("fix/18-remote-only", "remote")][1],
      "unknown: a remote tip whose object is not local is unknown")
t_fetched = table(report(fetch=True))
check(cls_of(t_fetched, "fix/18-remote-only", "remote") == br.UNLANDED,
      "control: after the fetch the same remote branch is judged on its content")

# ls-remote failing leaves the local half honest and the verdict partial.
git(primary, "remote", "set-url", "origin", str(base / "no-such-origin.git"))
rep_bad = report()
git(primary, "remote", "set-url", "origin", str(origin))
check(rep_bad.verdict == br.VERDICT_PARTIAL and any("remote branches not listed" in n for n in rep_bad.notes),
      "partial: a failed ls-remote is reported as partial with a note, never as an empty remote")
check(all(w == "local" for _, w in table(rep_bad)) and cls_of(table(rep_bad), "feat/10-squashed") == br.LANDED,
      "partial: the local half is still classified")

# An unreadable repo is unknown with a reason.
rep_none = br.build_report(base / "not-a-repo-dir", fetch=False, issue_state_fn=issue_state, open_pr_heads_fn=lambda: set())
check(rep_none.verdict == br.VERDICT_UNKNOWN and rep_none.reason and not rep_none.rows,
      "unknown: an unreadable repo has no rows and a reason, not an empty clean list")


# ---- orphan folders -------------------------------------------------------

(base / "alpha-wt-nav-41af40a").mkdir()                       # empty, non-numeric suffix
(base / "alpha-wt-full").mkdir()
(base / "alpha-wt-full" / "leftover.txt").write_text("x\n", encoding="utf-8")
(base / "alpha-wt-gitfile").mkdir()
(base / "alpha-wt-gitfile" / ".git").write_text("gitdir: nowhere\n", encoding="utf-8")
(base / "alpha-lite-wt-3").mkdir()                            # a different repo's worktree
(base / "alpha-wt-note.txt").write_text("a file, not a folder\n", encoding="utf-8")
(base / "beta-wt-1").mkdir()

orph = {Path(o.path).name: o.cls for o in report().orphans}
check(orph.get("alpha-wt-nav-41af40a") == "orphan-empty", f"orphans: an unregistered empty folder with a non-numeric suffix is reported (got {orph})")
check(orph.get("alpha-wt-full") == "orphan-has-content", "orphans: an unregistered folder with content is reported as such")
check(orph.get("alpha-wt-gitfile") == "orphan-with-git-file", "orphans: an unregistered folder with a .git entry is reported as such")
check("alpha-wt-16" not in orph, "orphans: a registered worktree is not an orphan")
check("alpha-lite-wt-3" not in orph and "beta-wt-1" not in orph and "alpha-wt-note.txt" not in orph,
      "orphans: another repo's worktree folder and a plain file are ignored")
check((base / "alpha-wt-full" / "leftover.txt").exists() and (base / "alpha-wt-nav-41af40a").exists(),
      "orphans: report-only, no folder is removed")
check({Path(o.path).name for o in br.orphans_report(primary).orphans} == set(orph),
      "orphans: the orphans-only report finds the same folders")
orph_from_wt = {Path(o.path).name for o in br.orphans_report(wt16).orphans}
check(orph_from_wt == set(orph), "orphans: run from a linked worktree, the primary's name is still the glob root")


# ---- CLI shape ---------------------------------------------------------------

def run_cli(*argv: str) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        br.main(list(argv))
    return buf.getvalue()


br_issue_state_real = br.make_gh_lookups
br.make_gh_lookups = lambda slug: (issue_state, lambda: set())  # keep the CLI run off the network
try:
    out = run_cli("branches", str(primary), "--no-fetch")
finally:
    br.make_gh_lookups = br_issue_state_real
lines = out.strip().splitlines()
check(any(ln.startswith("BRANCH=feat/10-squashed WHERE=local SHA=") and "CLASS=landed" in ln for ln in lines),
      f"cli: one BRANCH= line per ref with where, sha and class (got {out[:300]!r})")
check(lines[-1].startswith("VERDICT=") and any(ln.startswith("SUMMARY landed=") for ln in lines)
      and any(ln.startswith("ORPHAN=") for ln in lines) and any(ln.startswith("ORPHANS=") for ln in lines),
      "cli: ORPHAN, SUMMARY and VERDICT lines are present, VERDICT last")
out = run_cli("orphans", str(primary))
check("BRANCH=" not in out and "ORPHAN=" in out and "VERDICT=" not in out, "cli: orphans prints only the orphan section")
out = run_cli("branches", str(base / "missing"), "--no-fetch")
check(out.startswith("VERDICT=unknown") and "REASON=no such path" in out, "cli: a missing repo path is unknown, not clean")

_h.report_and_exit("test_branch_residue")
