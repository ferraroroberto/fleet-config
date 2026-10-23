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
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import branch_residue as br  # noqa: E402
import worktree_residue as wr  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from residue_fixtures import make_origin_and_primary  # noqa: E402

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

check(wr.classify_orphan(True, True)[0] == "orphan-with-git-file"
      and wr.classify_orphan(False, True)[0] == "orphan-has-content"
      and wr.classify_orphan(False, False)[0] == "orphan-empty"
      and wr.classify_orphan(False, None)[0] == br.UNKNOWN,
      "classify_orphan: .git entry, content, empty, and unreadable are four distinct classes")


# ---- real git fixtures --------------------------------------------------

base, origin, primary, git, commit = make_origin_and_primary("br")


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

# ---- --delete (fleet-config#1528) -----------------------------------------------
# Every scenario gets its own throwaway origin + clone: a delete mutates refs.

class Fx:
    pass


def make_fixture(tag: str) -> Fx:
    f = Fx()
    f.root = Path(tempfile.mkdtemp(prefix=f"br-del-{tag}-")).resolve()
    f.origin = f.root / "origin.git"
    git(f.root, "init", "-q", "--bare", "-b", "main", str(f.origin))
    f.primary = f.root / "gamma"
    git(f.root, "clone", "-q", str(f.origin), str(f.primary))
    git(f.primary, "checkout", "-q", "-b", "main")
    commit(f.primary, "a.txt", "one\n", "base")
    git(f.primary, "push", "-q", "-u", "origin", "main")
    git(f.primary, "remote", "set-head", "origin", "main")

    def dsquash(name: str, filename: str) -> None:
        git(f.primary, "checkout", "-q", "-b", name, "main")
        commit(f.primary, filename, f"{name}\n", f"work on {name}")
        git(f.primary, "push", "-q", "origin", name)
        git(f.primary, "checkout", "-q", "main")
        commit(f.primary, filename, f"{name}\n", f"squash of {name}")
        git(f.primary, "push", "-q", "origin", "main")

    git(f.primary, "branch", "feat/22-landed-anc", "main")
    git(f.primary, "branch", "feat/24-open", "main")
    git(f.primary, "push", "-q", "origin", "feat/22-landed-anc", "feat/24-open")
    for n, fn in (("feat/21-landed-sq", "s21.txt"), ("feat/25-openpr", "s25.txt"),
                  ("feat/26-checkedout", "s26.txt"), ("feat/27-remote-only", "s27.txt")):
        dsquash(n, fn)
    git(f.primary, "branch", "-D", "feat/27-remote-only")  # origin keeps it; the object stays local
    git(f.primary, "checkout", "-q", "-b", "feat/23-unlanded", "main")
    commit(f.primary, "u23.txt", "unshipped\n", "unshipped work")
    git(f.primary, "push", "-q", "origin", "feat/23-unlanded")
    git(f.primary, "checkout", "-q", "main")
    git(f.primary, "branch", "housekeeping-2026", "main")
    git(f.primary, "worktree", "add", "-q", str(f.root / "gamma-wt-26"), "feat/26-checkedout")

    f.issues = {21: "closed", 22: "closed", 23: "closed", 24: "open", 25: "closed", 26: "closed", 27: "closed"}
    f.prs = {"feat/25-openpr"}
    f.factory = lambda: (lambda n: f.issues.get(n), lambda: set(f.prs))
    return f


def fx_report(f: Fx):
    return br.build_report(f.primary, fetch=False, issue_state_fn=lambda n: f.issues.get(n),
                           open_pr_heads_fn=lambda: set(f.prs))


def fx_local(f: Fx) -> set:
    return set(git(f.primary, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())


def fx_remote(f: Fx) -> dict:
    return {ln.split("\t")[1][len("refs/heads/"):]: ln.split("\t")[0]
            for ln in git(f.primary, "ls-remote", "--heads", "origin").splitlines()}


def by_name(outcomes, where=None) -> dict:
    return {o.name: o for o in outcomes if where is None or o.where == where}


# S1: local delete takes exactly the landed set; origin untouched without --remote.
f1 = make_fixture("local")
rep1 = fx_report(f1)
landed_local = {r.ref.name for r in rep1.rows if r.cls == br.LANDED and r.ref.where == "local"}
check(landed_local == {"feat/21-landed-sq", "feat/22-landed-anc"},
      f"delete fixture: the report finds exactly the two landed local branches (got {landed_local})")
local_before, remote_before = fx_local(f1), fx_remote(f1)
sha21 = git(f1.primary, "rev-parse", "feat/21-landed-sq")
out1 = br.delete_landed(f1.primary, rep1, f1.factory)
check(fx_local(f1) == local_before - landed_local,
      f"delete: only the landed local branches are gone; every other class stays (left {sorted(fx_local(f1))})")
check(fx_remote(f1) == remote_before, "delete: without --remote every origin ref is byte-identical")
check(all(o.action == "deleted" and o.where == "local" for o in out1) and len(out1) == 2,
      "delete: two local deletes, no remote action attempted")
lines1 = br.render_outcomes(out1)
check(f"DELETED=local:feat/21-landed-sq SHA={sha21} PROOF=" in "\n".join(lines1)
      and f"RESTORE=git branch feat/21-landed-sq {sha21}" in lines1,
      "delete: prints the sha, the proof and a restore command for a local delete")
restore = [ln for ln in lines1 if ln.startswith("RESTORE=")][0][len("RESTORE="):].split()
git(f1.primary, *restore[1:])
check(git(f1.primary, "rev-parse", "feat/21-landed-sq") == sha21, "delete: the printed local restore command brings the branch back at its sha")
check(lines1[-1] == "DELETE_SUMMARY deleted=2 skipped=0 failed=0", f"delete: summary line (got {lines1[-1]})")

# S1b: --remote deletes the landed remote tips, each proven on its own, and nothing else.
rep1b = fx_report(f1)
landed_remote = {r.ref.name for r in rep1b.rows if r.cls == br.LANDED and r.ref.where == "remote"}
check(landed_remote == {"feat/21-landed-sq", "feat/22-landed-anc", "feat/27-remote-only"},
      f"delete fixture: the remote landed set includes a branch with no local copy (got {landed_remote})")
remote_before = fx_remote(f1)
sha27 = remote_before["feat/27-remote-only"]
out1b = br.delete_landed(f1.primary, rep1b, f1.factory, remote=True)
check(fx_remote(f1) == {k: v for k, v in remote_before.items() if k not in landed_remote},
      f"delete --remote: exactly the landed origin refs are gone (left {sorted(fx_remote(f1))})")
check("feat/23-unlanded" in fx_remote(f1) and "feat/25-openpr" in fx_remote(f1) and "feat/26-checkedout" in fx_remote(f1)
      and "main" in fx_remote(f1), "delete --remote: unlanded, open-pr, checked-out and the default stay on origin")
rest = [ln for ln in br.render_outcomes(out1b) if ln.startswith("RESTORE=")]
check(f"RESTORE=git push origin {sha27}:refs/heads/feat/27-remote-only" in rest, "delete --remote: prints a push restore command")
git(f1.primary, *f"push origin {sha27}:refs/heads/feat/27-remote-only".split())
check(fx_remote(f1).get("feat/27-remote-only") == sha27, "delete --remote: the printed restore command recreates the remote branch")

# S2: explicit names, refusals and exit codes through the CLI.
f2 = make_fixture("names")
real_lookups = br.make_gh_lookups


def cli(f: Fx, *argv: str):
    """Run the CLI with `f`'s lookups standing in for `gh`; (exit code, stdout)."""
    buf, err = io.StringIO(), io.StringIO()
    br.make_gh_lookups = lambda slug: f.factory()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                code = br.main(list(argv))
            except SystemExit as exc:
                code = exc.code
    finally:
        br.make_gh_lookups = real_lookups
    return code, buf.getvalue()


code, out = cli(f2, "branches", str(f2.primary), "--no-fetch", "--delete", "--branch", "main", "--branch", "feat/26-checkedout",
                "--branch", "feat/23-unlanded", "--branch", "housekeeping-2026", "--branch", "feat/nope")
check(code == 1, f"names: refusing every named ref exits non-zero (got {code})")
for needle in ("REFUSED=local:main REASON=CLASS=default", "REFUSED=local:feat/26-checkedout REASON=CLASS=checked-out",
               "REFUSED=local:feat/23-unlanded REASON=CLASS=unlanded", "REFUSED=local:housekeeping-2026 REASON=CLASS=no-issue",
               "REFUSED=-:feat/nope"):
    check(needle in out, f"names: {needle.split(' REASON')[0]} is refused with its class")
check({"main", "feat/26-checkedout", "feat/23-unlanded", "housekeeping-2026"} <= fx_local(f2),
      "names: a refused branch is never deleted, even named explicitly")

code, out = cli(f2, "branches", str(f2.primary), "--no-fetch", "--delete", "--branch", "feat/21-landed-sq", "--branch", "main")
check(code == 1 and "DELETED=local:feat/21-landed-sq" in out and "feat/21-landed-sq" not in fx_local(f2)
      and "feat/22-landed-anc" in fx_local(f2),
      "names: only the named landed branch goes; a refused sibling in the same call still makes the exit non-zero")

code, out = cli(f2, "branches", str(f2.primary), "--no-fetch", "--delete", "--branch", "feat/27-remote-only")
check(code == 1 and "SKIPPED=remote:feat/27-remote-only REASON=a remote delete needs --remote" in out
      and "feat/27-remote-only" in fx_remote(f2), "names: a remote-only branch without --remote is skipped, origin untouched")

check(cli(f2, "branches", str(f2.primary), "--branch", "x")[0] == 2 and cli(f2, "branches", str(f2.primary), "--remote")[0] == 2,
      "cli: --branch / --remote without --delete is a usage error")

remote_names = set(fx_remote(f2))
code, out = cli(f2, "branches", str(f2.primary), "--no-fetch", "--delete")
check(code == 0 and fx_local(f2) == {"main", "feat/23-unlanded", "feat/24-open", "feat/25-openpr", "feat/26-checkedout", "housekeeping-2026"},
      f"sweep: --delete with no names takes every landed local branch and exits 0 (left {sorted(fx_local(f2))})")
check(set(fx_remote(f2)) == remote_names, "sweep: a local-only --delete leaves origin exactly as it was")

# S3: a tip that moves is never deleted.
f3 = make_fixture("moved")
rep3 = fx_report(f3)
git(f3.primary, "checkout", "-q", "feat/21-landed-sq")
commit(f3.primary, "late21.txt", "late\n", "late local work")
git(f3.primary, "checkout", "-q", "main")
other = f3.root / "other"
git(f3.root, "clone", "-q", str(f3.origin), str(other))


def push_from_other(branch: str, fname: str) -> str:
    git(other, "fetch", "-q", "origin")
    git(other, "checkout", "-q", "-B", branch, f"origin/{branch}")
    commit(other, fname, "late\n", f"late push to {branch}")
    git(other, "push", "-q", "origin", branch)
    return git(other, "rev-parse", "HEAD")


moved22 = push_from_other("feat/22-landed-anc", "late22.txt")
o3 = br.delete_landed(f3.primary, rep3, f3.factory, names=["feat/21-landed-sq", "feat/22-landed-anc"], remote=True)
o3l, o3r = by_name(o3, "local"), by_name(o3, "remote")
check(o3l["feat/21-landed-sq"].action == "skipped" and "tip moved since the report" in o3l["feat/21-landed-sq"].detail
      and "feat/21-landed-sq" in fx_local(f3), "moved: a local tip that moved after the report is skipped, not deleted")
check(o3r["feat/22-landed-anc"].action == "skipped" and fx_remote(f3)["feat/22-landed-anc"] == moved22,
      "moved: a remote tip pushed after the report is skipped and left at its new sha")

# The race the report-time check cannot see: a push lands after the fresh proof, before the delete.
rep3b = fx_report(f3)
race = {"fired": False}


def racing_factory():
    if not race["fired"]:
        race["fired"] = True
        race["sha"] = push_from_other("feat/27-remote-only", "late27.txt")
    return f3.factory()


o3b = by_name(br.delete_landed(f3.primary, rep3b, racing_factory, names=["feat/27-remote-only"], remote=True), "remote")
check(race["fired"] and o3b["feat/27-remote-only"].action == "stays" and "tip moved after the proof" in o3b["feat/27-remote-only"].detail,
      f"lease: a push between the proof and the delete is rejected by the lease and reported STAYS (got {o3b})")
check(fx_remote(f3).get("feat/27-remote-only") == race.get("sha"), "lease: the racing push survives on origin")
check("STAYS=remote:feat/27-remote-only (tip moved)" in "\n".join(br.render_outcomes(list(o3b.values()))),
      "lease: STAYS is rendered with the tip-moved marker")

# S4: an open PR, a failed PR lookup and a failed issue lookup all skip.
f4 = make_fixture("pr")
f4.prs = set()
rep4 = fx_report(f4)  # reported landed: the PR is opened only afterwards
check("feat/25-openpr" in {r.ref.name for r in rep4.rows if r.cls == br.LANDED and r.ref.where == "local"},
      "open-pr fixture: the branch is landed in the stale report")
f4.prs = {"feat/25-openpr"}
o4 = by_name(br.delete_landed(f4.primary, rep4, f4.factory, names=["feat/25-openpr"], remote=True), "local")
check(o4["feat/25-openpr"].action == "skipped" and "CLASS=open-pr" in o4["feat/25-openpr"].detail
      and "feat/25-openpr" in fx_local(f4) and "feat/25-openpr" in fx_remote(f4),
      "open-pr: a head that gained an open PR since the report is skipped, local and remote")
o4s = br.delete_landed(f4.primary, rep4, f4.factory, remote=True)
check(fx_local(f4) == {"main", "feat/23-unlanded", "feat/24-open", "feat/25-openpr", "feat/26-checkedout", "housekeeping-2026"}
      and "feat/25-openpr" in fx_remote(f4) and by_name(o4s, "remote")["feat/25-openpr"].action == "skipped",
      "open-pr: in a sweep the PR head is skipped while the other landed refs go")
f4b = make_fixture("lookups")
rep4b = fx_report(f4b)
no_pr_lookup = lambda: (lambda n: f4b.issues.get(n), lambda: None)  # noqa: E731
o4b = br.delete_landed(f4b.primary, rep4b, no_pr_lookup, remote=True)
check(o4b and all(o.action == "skipped" and "CLASS=unknown" in o.detail for o in o4b)
      and fx_local(f4b) >= {"feat/21-landed-sq", "feat/22-landed-anc"},
      "unknown: a failed PR lookup skips every target instead of reading as 'no PR'")
no_issue_lookup = lambda: (lambda n: None, lambda: set())  # noqa: E731
o4c = br.delete_landed(f4b.primary, rep4b, no_issue_lookup)
check(o4c and all(o.action == "skipped" and "CLASS=unknown" in o.detail for o in o4c) and "feat/21-landed-sq" in fx_local(f4b),
      "unknown: a failed issue lookup skips every target")

# S5: a failing delete does not stop the others and the exit code says so.
f5 = make_fixture("fail")
lock = f5.primary / ".git" / "refs" / "heads" / "feat" / "21-landed-sq.lock"
lock.parent.mkdir(parents=True, exist_ok=True)
lock.write_text("", encoding="utf-8")
code, out = cli(f5, "branches", str(f5.primary), "--no-fetch", "--delete")
check(code == 1 and "FAILED=local:feat/21-landed-sq REASON=" in out,
      f"failure: a git failure is reported with git's message and exits non-zero (got {code}, {out[-300:]!r})")
check("DELETED=local:feat/22-landed-anc" in out and "feat/22-landed-anc" not in fx_local(f5),
      "failure: later targets are still attempted after one fails")
check("DELETE_SUMMARY deleted=1 skipped=0 failed=1" in out, "failure: the summary counts the failure")

# The report path never mutates: `branches` without --delete leaves every ref byte-identical.
f6 = make_fixture("readonly")
snap6 = lambda: (git(f6.primary, "for-each-ref", "refs/"), git(f6.primary, "ls-remote", "origin"),  # noqa: E731
                 git(f6.primary, "worktree", "list", "--porcelain"))
before6 = snap6()
cli(f6, "branches", str(f6.primary), "--no-fetch")
check(before6 == snap6(), "report-only: `branches` without --delete leaves refs, origin and worktrees byte-identical")


_h.report_and_exit("test_branch_residue")
