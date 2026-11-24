"""Tests for the owner keep-list in skills/_lib/branch_residue.py (fleet-config#2224).

A branch the owner keeps on purpose is listed in `branch-keep-list.txt` in the hooks state dir and
must never be deleted, even when it is provably landed:

  * owner-keep -- a landed branch on the list (bare name, `<repo>:<glob>`) is classed owner-keep, local
    and remote, counted under keep, and survives `--delete --remote`; naming it with `--branch` is refused;
  * unchanged -- a landed branch off the list, or on another repo's entry, is still deleted;
  * unreadable, its own state -- a directory, invalid UTF-8 or a malformed line in place of the list makes
    the run delete nothing, exit 1, and say why, in the report as well.

Real throwaway git repos (bare origin + clone), `gh` lookups injected, nothing outside a temp dir.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/residue/test_branch_residue_keep_list.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import branch_residue as br  # noqa: E402
from residue import keep_list  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from residue_fixtures import make_origin_and_primary  # noqa: E402

_h = CheckHarness()
check = _h.check

# ---- parsing -------------------------------------------------------------------

kl = keep_list.parse("# a comment\n\nfeat/9-*\nalpha: wip/*\r\n")
check(kl.error == "" and [(e.repo, e.pattern) for e in kl.entries] == [("", "feat/9-*"), ("alpha", "wip/*")],
      "parse: comments and blanks are skipped, `<repo>:<glob>` and a bare glob both parse")
check(kl.match("any-repo", "feat/9-x") and kl.match("ALPHA", "wip/a/b") and not kl.match("delta", "wip/a")
      and not kl.match("alpha", "Feat/9-x"),
      "match: a bare glob applies everywhere, a repo prefix only to that repo (case-blind), globs are case-sensitive")
check(keep_list.parse(":x").error and keep_list.parse("alpha:").error,
      "parse: an entry with an empty repo or glob is an error, not an ignored line")

# ---- real git ------------------------------------------------------------------

base, origin, primary, git, commit = make_origin_and_primary("brk")
KEEP_FILE = Path(os.environ["CLAUDE_HOOKS_STATE_DIR"]) / keep_list.KEEP_LIST_FILENAME
check(keep_list.load(KEEP_FILE) == keep_list.KeepList(), "load: a missing file is an empty list")

NAMES = ("feat/21-by-name", "feat/22-by-glob", "feat/23-unlisted", "feat/24-other-repo")
for n in NAMES:
    git(primary, "branch", n, "main")
git(primary, "push", "-q", "origin", *NAMES)
commit(primary, "b.txt", "two\n", "main moves on")
git(primary, "push", "-q", "origin", "main")


def lookups():
    return (lambda n: "closed", lambda: set(), lambda n: [], lambda name: [])


real_lookups = br.make_gh_lookups


def cli(*argv: str):
    """Run the CLI with injected lookups; (exit code, stdout)."""
    buf, err = io.StringIO(), io.StringIO()
    br.make_gh_lookups = lambda slug: lookups()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = br.main(list(argv))
    finally:
        br.make_gh_lookups = real_lookups
    return code, buf.getvalue()


def rows():
    return {(r.ref.name, r.ref.where): r for r in br.build_report(primary, fetch=False, issue_state_fn=lookups()[0],
                                                                  open_pr_heads_fn=lookups()[1], closing_prs_fn=lookups()[2],
                                                                  own_prs_fn=lookups()[3]).rows}


def refs() -> tuple:
    return git(primary, "for-each-ref", "refs/"), git(primary, "ls-remote", "origin")


before = rows()
check(all(before[(n, w)].cls == br.LANDED for n in NAMES for w in ("local", "remote")),
      "fixture: every branch is landed before the list exists")

KEEP_FILE.write_text(f"# mine\nfeat/21-by-name\n{primary.name}:feat/22-*\ndelta:feat/24-other-repo\n", encoding="utf-8")
kept = rows()
check(all(kept[(n, w)].cls == br.OWNER_KEEP for n in NAMES[:2] for w in ("local", "remote")),
      "keep-list: a landed branch on the list is owner-keep, local and remote, by bare name and by repo glob")
check("feat/21-by-name" in kept[("feat/21-by-name", "local")].reason, "keep-list: the reason names the matching entry")
check(all(kept[(n, w)].cls == br.LANDED for n in NAMES[2:] for w in ("local", "remote")),
      "keep-list: an unlisted branch and another repo's entry keep their class")
check(br.summarize(list(kept.values())) == {"landed": 4, "superseded": 0, "unlanded": 0, "keep": 6, "unknown": 0},  # 4 owner-keep + main, local and remote
      f"keep-list: owner-keep rows count under keep, not landed (got {br.summarize(list(kept.values()))})")

code, out = cli("branches", str(primary), "--no-fetch", "--delete", "--remote")
local_after = set(git(primary, "for-each-ref", "--format=%(refname:short)", "refs/heads").split())
remote_after = {ln.split("\t")[1] for ln in git(primary, "ls-remote", "--heads", "origin").splitlines()}
check({"feat/21-by-name", "feat/22-by-glob"} <= local_after
      and {"refs/heads/feat/21-by-name", "refs/heads/feat/22-by-glob"} <= remote_after,
      f"keep-list: --delete --remote leaves the listed branches on both sides (out {out[-300:]!r})")
check(not ({"feat/23-unlisted", "feat/24-other-repo"} & local_after) and code == 0
      and "DELETED=local:feat/23-unlisted" in out and "DELETED=remote:feat/24-other-repo" in out
      and "DELETED=local:feat/21" not in out and "DELETED=remote:feat/22" not in out,
      f"keep-list: the unlisted landed branches are still deleted (exit {code})")
code, out = cli("branches", str(primary), "--no-fetch", "--delete", "--branch", "feat/21-by-name")
check(code == 1 and "REFUSED=local:feat/21-by-name REASON=CLASS=owner-keep" in out,
      f"keep-list: naming a listed branch is refused with its class (got {out[-200:]!r})")

# An unreadable list is its own state: each broken shape deletes nothing and says why.
git(primary, "branch", "feat/25-landed", "main")
git(primary, "push", "-q", "origin", "feat/25-landed")
KEEP_FILE.unlink()
for label, setup in (("a directory in its place", lambda: KEEP_FILE.mkdir()),
                     ("invalid utf-8", lambda: (KEEP_FILE.rmdir(), KEEP_FILE.write_bytes(b"feat/1\xff\xfe\n"))),
                     ("a malformed line", lambda: KEEP_FILE.write_text("alpha:\n", encoding="utf-8"))):
    setup()
    snapshot = refs()
    code, out = cli("branches", str(primary), "--no-fetch", "--delete", "--remote")
    check(code == 1 and "REFUSED=-:* REASON=owner keep-list unreadable" in out and "DELETE_SUMMARY deleted=0" in out
          and "VERDICT=partial" in out and "NOTE=owner keep-list unreadable" in out,
          f"unreadable ({label}): exit 1, partial verdict, the reason printed, nothing deleted (got {out[-300:]!r})")
    check(refs() == snapshot, f"unreadable ({label}): every local and remote ref is byte-identical")
    _, plain = cli("branches", str(primary), "--no-fetch")
    check("NOTE=owner keep-list unreadable" in plain and "VERDICT=partial" in plain,
          f"unreadable ({label}): the plain report says so too")

_h.report_and_exit("test_branch_residue_keep_list")
