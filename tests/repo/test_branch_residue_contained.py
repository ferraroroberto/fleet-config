"""Tests for the no-issue "contained in the default branch" proof in skills/_lib/branch_residue.py (fleet-config#1958).

A branch whose name carries no issue number has no closed issue to prove, so its
landed proof is containment in the default branch: 0 commits ahead of it, or
`git cherry` listing only `-` lines (one per commit ahead, since cherry skips
merge commits). The ways it must refuse matter most:

  * keep -- a unique commit, an equivalent commit plus a unique one, a merge
    commit, an empty commit, an open PR on the head;
  * unknown, its own state -- an unreadable `git cherry`, an unreadable ahead
    count, a remote tip whose object is not local, a failed PR lookup;
  * `--delete` takes exactly the contained refs, local and remote, and a branch
    that gained a commit after the report is skipped.

Real throwaway git repos (bare origin + clone), `gh` lookups injected, nothing
outside a temp dir.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_branch_residue_contained.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
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
    base = dict(is_default=False, checked_out=False, issue=None, issue_state=None, tip_readable=True,
                merged=None, merged_how="", open_pr=False)
    base.update(over)
    return br.classify_ref(**base)


check(cr(contained=True, contained_how="0 commits ahead")[0] == br.LANDED
      and "contained" in cr(contained=True, contained_how="0 commits ahead")[1],
      "classify: a no-issue branch contained in the default branch is landed, with `contained` and the proof in the reason")
check(cr(contained=False)[0] == br.NO_ISSUE, "classify: a no-issue branch with a unique commit stays no-issue (keep)")
check(cr(contained=None, contained_how="cherry failed")[0] == br.UNKNOWN,
      "classify: an unestablished containment proof is unknown, never landed")
check(cr(tip_readable=False, contained=None)[0] == br.UNKNOWN, "classify: a no-issue tip that is not present locally is unknown")
check(cr(contained=True, open_pr=True)[0] == br.OPEN_PR and cr(contained=True, open_pr=None)[0] == br.UNKNOWN,
      "classify: a contained no-issue tip still needs a proven absence of an open PR")
check(cr(is_default=True, contained=True)[0] == br.DEFAULT and cr(checked_out=True, contained=True)[0] == br.CHECKED_OUT,
      "classify: containment never outranks default or checked-out")
check(cr(issue=7, issue_state="open", contained=True)[0] == br.OPEN_ISSUE,
      "classify: containment is a no-issue proof only; an open issue still wins")


# ---- real git fixtures --------------------------------------------------

class Fx:
    pass


def make_fixture(tag: str) -> Fx:
    f = Fx()
    f.base, f.origin, f.primary, f.git, f.commit = make_origin_and_primary(f"brc-{tag}")
    f.prs = set()
    f.lookups = lambda: (lambda n: None, lambda: set(f.prs), lambda n: [], lambda name: [])
    return f


def report(f: Fx, prs=None, fetch=False):
    return br.build_report(f.primary, fetch=fetch, issue_state_fn=lambda n: None,
                           open_pr_heads_fn=(lambda: set(f.prs)) if prs is None else prs,
                           closing_prs_fn=lambda n: [], own_prs_fn=lambda name: [])


def table(rep):
    return {(r.ref.name, r.ref.where): (r.cls, r.reason) for r in rep.rows}


def cls_of(t, name, where="local"):
    return t.get((name, where), ("<missing>", ""))[0]


def local_refs(f: Fx) -> set:
    return set(f.git(f.primary, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())


def remote_refs(f: Fx) -> set:
    return {ln.split("\t")[1][len("refs/heads/"):] for ln in f.git(f.primary, "ls-remote", "--heads", "origin").splitlines()}


real_lookups = br.make_gh_lookups


def cli(f: Fx, *argv: str):
    """Run the CLI with `f`'s lookups standing in for `gh`; (exit code, stdout)."""
    buf, err = io.StringIO(), io.StringIO()
    br.make_gh_lookups = lambda slug: f.lookups()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                code = br.main(list(argv))
            except SystemExit as exc:
                code = exc.code
    finally:
        br.make_gh_lookups = real_lookups
    return code, buf.getvalue()


f = make_fixture("main")
git, commit, p = f.git, f.commit, f.primary

git(p, "checkout", "-q", "-b", "housekeeping-2026", "main")  # one unshipped commit
commit(p, "h2026.txt", "unshipped\n", "unshipped housekeeping")
git(p, "branch", "chore/context-purge-zero", "main")  # 0 commits ahead of origin/main
git(p, "checkout", "-q", "-b", "chore/revendor-eq", "main")  # one commit, patch-equivalent on main
commit(p, "e11.txt", "x\n", "revendor")
git(p, "checkout", "-q", "-b", "chore/mixed", "chore/revendor-eq")  # the equivalent commit plus one unique
commit(p, "m11.txt", "unique\n", "unique on top")
git(p, "checkout", "-q", "-b", "side-11", "main")
commit(p, "g11.txt", "x\n", "side")
git(p, "checkout", "-q", "-b", "chore/merge-commit", "main")  # its one commit is on main, plus a merge commit
git(p, "merge", "-q", "--no-ff", "-m", "merge side", "side-11")
git(p, "checkout", "-q", "-b", "chore/empty-commit", "main")
git(p, "commit", "-q", "--allow-empty", "-m", "empty")
git(p, "branch", "chore/open-pr-zero", "main")
git(p, "checkout", "-q", "main")
git(p, "branch", "-D", "side-11")
commit(p, "e11.txt", "x\n", "landed copy of revendor")
commit(p, "g11.txt", "x\n", "landed copy of side")
commit(p, "e11.txt", "later\n", "later edit, so merge-tree conflicts")
git(p, "push", "-q", "origin", "main")
NOISSUE = ("chore/context-purge-zero", "chore/revendor-eq", "chore/mixed", "chore/merge-commit", "chore/empty-commit",
           "chore/open-pr-zero")
git(p, "push", "-q", "origin", *NOISSUE)
git(p, "fetch", "-q", "origin")
f.prs = {"chore/open-pr-zero"}

check(wr.head_merged(p, git(p, "rev-parse", "chore/revendor-eq"), "origin/main")[0] is not True,
      "fixture: the cherry-only branch is NOT provable by the merge-tree proof, so cherry is what lands it")
t = table(report(f))
for nm in ("chore/context-purge-zero", "chore/revendor-eq"):
    check(all(cls_of(t, nm, w) == br.LANDED for w in ("local", "remote")) and "contained" in t[(nm, "local")][1],
          f"contained: {nm} is landed, local and remote, with `contained` in the reason (got {t.get((nm, 'local'))})")
check("0 commits ahead" in t[("chore/context-purge-zero", "local")][1] and "git cherry" in t[("chore/revendor-eq", "local")][1],
      "contained: the reason names which proof landed it")
for nm in ("chore/mixed", "chore/merge-commit", "chore/empty-commit", "housekeeping-2026"):
    check(cls_of(t, nm) == br.NO_ISSUE, f"contained: {nm} carries something main lacks, so it stays keep (got {t.get((nm, 'local'))})")
check(cls_of(t, "chore/open-pr-zero") == br.OPEN_PR, "contained: a contained branch with an open PR is not landed")
tp = table(report(f, prs=lambda: None))
check(cls_of(tp, "chore/context-purge-zero") == br.UNKNOWN and cls_of(tp, "chore/revendor-eq") == br.UNKNOWN,
      "contained: a failed PR lookup turns a contained no-issue branch into unknown, not landed")

# Containment facts that cannot be read are unknown, never landed.
real_run_git = br.git_run.run_git


def failing(sub: str):
    def run(args, **kw):
        if sub in args:
            return subprocess.CompletedProcess(args, 128, "", "fatal: injected failure")
        return real_run_git(args, **kw)
    return run


try:
    br.git_run.run_git = failing("cherry")
    tc = table(report(f))
    br.git_run.run_git = failing("rev-list")
    tr = table(report(f))
finally:
    br.git_run.run_git = real_run_git
check(cls_of(tc, "chore/revendor-eq") == br.UNKNOWN and cls_of(tc, "chore/mixed") == br.UNKNOWN
      and "cherry" in tc[("chore/revendor-eq", "local")][1],
      f"contained: an unreadable `git cherry` is unknown (got {tc.get(('chore/revendor-eq', 'local'))})")
check(cls_of(tc, "chore/context-purge-zero") == br.LANDED, "contained: the 0-ahead proof does not need cherry")
check(all(cls_of(tr, nm) == br.UNKNOWN for nm in ("chore/context-purge-zero", "chore/revendor-eq", "housekeeping-2026")),
      "contained: an unreadable ahead count is unknown for every no-issue branch")

beta = f.base / "beta"
git(f.base, "clone", "-q", str(f.origin), str(beta))
git(beta, "checkout", "-q", "-b", "chore/remote-only", "origin/main")
commit(beta, "r11.txt", "remote\n", "remote-only")
git(beta, "push", "-q", "origin", "chore/remote-only")
tu = table(report(f))
check(cls_of(tu, "chore/remote-only", "remote") == br.UNKNOWN and "not present locally" in tu[("chore/remote-only", "remote")][1],
      "contained: a no-issue remote tip whose object is not local is unknown")

# --delete takes exactly the contained branches, local and remote, and prints the restore command.
sha_zero = git(p, "rev-parse", "chore/context-purge-zero")
gone = {"chore/context-purge-zero", "chore/revendor-eq"}
code, out = cli(f, "branches", str(p), "--no-fetch", "--delete", "--remote")
check(code == 0 and not (gone & local_refs(f)) and not (gone & remote_refs(f)),
      f"contained: --delete removes the contained no-issue branches, local and remote (got {code}, {out[-500:]!r})")
check({"chore/mixed", "chore/merge-commit", "chore/empty-commit", "chore/open-pr-zero", "housekeeping-2026"} <= local_refs(f)
      and {"chore/mixed", "chore/merge-commit", "chore/empty-commit", "chore/open-pr-zero", "chore/remote-only"} <= remote_refs(f),
      "contained: --delete leaves the unique-commit, open-PR and unfetched branches, local and remote")
check(f"RESTORE=git branch chore/context-purge-zero {sha_zero}" in out and "PROOF=contained" in out,
      "contained: the delete prints the proof and a restore command")
code, out = cli(f, "branches", str(p), "--no-fetch", "--delete", "--branch", "chore/mixed")
check(code == 1 and "REFUSED=local:chore/mixed REASON=CLASS=no-issue" in out and "chore/mixed" in local_refs(f),
      "contained: naming a no-issue branch with a unique commit is refused")

# A contained branch that gains a commit between the report and the delete is skipped.
g = make_fixture("moved")
g.git(g.primary, "branch", "chore/moves", "main")
rep = report(g)
check(cls_of(table(rep), "chore/moves") == br.LANDED, "fixture: the branch is landed in the stale report")
g.git(g.primary, "checkout", "-q", "chore/moves")
g.commit(g.primary, "late.txt", "late\n", "late work")
g.git(g.primary, "checkout", "-q", "main")
outcomes = {o.name: o for o in br.delete_landed(g.primary, rep, g.lookups, names=["chore/moves"])}
check(outcomes["chore/moves"].action == "skipped" and "chore/moves" in local_refs(g),
      "contained: a branch that gained a commit after the report is skipped, not deleted")


_h.report_and_exit("test_branch_residue_contained")
