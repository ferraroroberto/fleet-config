"""The /upstream-sync watermark helper: forward-only, contiguous, and fetch-only.

Drives skills/_lib/upstream_sync against throwaway git repos: a source repo that keeps
merging work and a local copy that diverged from it. Proves the watermark only moves over
a contiguous run of merged or skipped items (a failed middle item holds it), the
`[upstream]` table round-trips through `.fleet.toml` without touching the rest of the file,
the survey lists the source's first-parent work after the watermark in order and marks
what the local tree already carries, the source remote can be fetched but not pushed, and
the licence check refuses anything it cannot confirm as permissive. No real repo is touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_upstream_sync.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import tomllib
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402
from upstream_sync import cli, ledger, source  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"


def _git(repo: Path, *args: str) -> str:
    done = run_git(["-C", str(repo), *args])
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr}")
    return done.stdout.strip()


def _commit(repo: Path, name: str, body: str, subject: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


def _init(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    _no_hooks(repo)


def _no_hooks(repo: Path) -> None:
    # `core.hooksPath` -> an empty dir: this machine carries a global commit hook
    # that refuses the fixture's author email.
    empty = repo.parent / "no-hooks"
    empty.mkdir(exist_ok=True)
    _git(repo, "config", "core.hooksPath", str(empty))


def _cli(argv: list) -> tuple:
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


# --- the contiguous-advance rule (pure) -------------------------------------------------

order = ["a1", "b2", "c3", "d4"]
check(ledger.advance_watermark("w0", order, merged={"a1"}, skipped=set()) == "a1",
      "advance: a merged first item moves the watermark onto it")
check(ledger.advance_watermark("w0", order, merged={"a1", "c3"}, skipped=set()) == "a1",
      "advance: a failed middle item holds the watermark before it, even with a later item merged")
check(ledger.advance_watermark("w0", order, merged={"c3"}, skipped=set()) == "w0",
      "advance: nothing contiguous from the watermark leaves it where it was")
check(ledger.advance_watermark("w0", order, merged={"a1", "c3"}, skipped={"b2"}) == "c3",
      "advance: a recorded skip is walked over like a merged item")
check(ledger.advance_watermark("w0", order, merged={"a1", "b2", "c3", "d4"}, skipped=set()) == "d4",
      "advance: every item done moves the watermark to the source head")
check(ledger.advance_watermark("w0", [], merged=set(), skipped=set()) == "w0",
      "advance: no new source work leaves the watermark unchanged")

# --- the [upstream] table in .fleet.toml ------------------------------------------------

root = Path(tempfile.mkdtemp(prefix="upstream-sync-"))
try:
    toml_repo = root / "toml"
    toml_repo.mkdir()
    original = '# card\nlayer = "working-pipe"\nicon = "x"\n\n[worktree]\ncopy = ["a"]\n'
    (toml_repo / ".fleet.toml").write_text(original, encoding="utf-8")
    state, status = ledger.read_state(toml_repo)
    check(state is None and status == "absent", f"read_state: no [upstream] table reads as absent ({status})")

    st = ledger.UpstreamState(url="https://example.invalid/src.git", branch="main", synced_sha="f" * 40,
                              skipped=[ledger.Skip(sha="e" * 40, reason='needs a "cloud" account')])
    ledger.write_state(toml_repo, st)
    text = (toml_repo / ".fleet.toml").read_text(encoding="utf-8")
    parsed = tomllib.loads(text)
    check(text.startswith(original.rstrip("\n")), "write_state: the rest of .fleet.toml is kept byte for byte")
    check(parsed["worktree"] == {"copy": ["a"]} and parsed["layer"] == "working-pipe",
          "write_state: other tables still parse unchanged")
    back, status = ledger.read_state(toml_repo)
    check(status == "ok" and back == st, f"read_state: the written table round-trips ({back})")

    st2 = ledger.UpstreamState(url=st.url, branch="main", synced_sha="d" * 40, skipped=list(st.skipped))
    ledger.write_state(toml_repo, st2)
    text2 = (toml_repo / ".fleet.toml").read_text(encoding="utf-8")
    check(text2.count("[upstream]") == 1 and text2.count("[[upstream.skipped]]") == 1,
          "write_state: a rewrite replaces the table, never duplicates it")
    check(ledger.read_state(toml_repo)[0].synced_sha == "d" * 40, "write_state: the rewrite carries the new watermark")

    (toml_repo / ".fleet.toml").write_text("[upstream]\nurl = 3\n", encoding="utf-8")
    check(ledger.read_state(toml_repo)[1] == "invalid", "read_state: a malformed [upstream] table is invalid, not absent")

    # --- the git half: source remote, survey, licence -----------------------------------

    src = root / "src"
    _init(src)
    _commit(src, "LICENSE", MIT, "init")
    base = _commit(src, "core.py", "x = 1\n", "core (#1)")

    local = root / "local"
    _git(root, "clone", "-q", str(src), str(local))
    _git(local, "config", "user.email", "test@example.invalid")
    _git(local, "config", "user.name", "Test")
    _git(local, "config", "commit.gpgsign", "false")
    _no_hooks(local)
    _git(local, "remote", "remove", "origin")
    _commit(local, ".fleet.toml", 'layer = "working-pipe"\n', "local card")

    code, out = _cli(["init", str(src), "--dry-run", "--repo", str(local)])
    check(code == 0 and f"SOURCE_HEAD={base}" in out and "WRITE=skipped" in out
          and ledger.read_state(local)[1] == "absent", f"init --dry-run: reports the head and writes nothing ({out!r})")
    code, out = _cli(["init", str(src), "--branch", "main", "--repo", str(local)])
    check(code == 0 and f"SYNCED_SHA={base}" in out, f"init: the first run sets the watermark to the source head ({out!r})")
    check(_git(local, "remote", "get-url", "--push", source.REMOTE) == source.NO_PUSH,
          "init: the source remote is fetch-only (its push url is disabled)")
    code, out = _cli(["init", str(src), "--repo", str(local)])
    check(code == 2 and "already" in out, f"init: a second init is refused, never resetting the watermark ({out!r})")
    _git(local, "commit", "-q", "-am", "set watermark")

    feat_a = _commit(src, "a.py", "a = 1\n", "feat: a (#2)")
    feat_b = _commit(src, "b.py", "b = 1\n", "feat: b (#3)")
    _git(src, "checkout", "-q", "-b", "topic")
    _commit(src, "c.py", "c = 1\n", "work on c")
    _git(src, "checkout", "-q", "main")
    _git(src, "merge", "-q", "--no-ff", "topic", "-m", "Merge pull request #4 from someone/topic")
    merge_c = _git(src, "rev-parse", "HEAD")

    # The local copy already carries feature b (same patch, its own commit).
    (local / "b.py").write_text("b = 1\n", encoding="utf-8")
    _git(local, "add", "-A")
    _git(local, "commit", "-q", "-m", "bring b in early")

    head_before = _git(local, "rev-parse", "HEAD")
    code, out = _cli(["survey", "--repo", str(local)])
    items = [line for line in out.splitlines() if line.startswith("ITEM ")]
    check(code == 0 and len(items) == 3, f"survey: lists the three first-parent items after the watermark ({out!r})")
    check([i.split("sha=")[1].split()[0] for i in items] == [feat_a, feat_b, merge_c],
          "survey: items come oldest first, the side branch folded into its merge")
    check("pr=2" in items[0] and "pr=3" in items[1] and "pr=4" in items[2],
          "survey: each item names its source PR number from a squash or a merge subject")
    check("present=yes" in items[1] and "present=no" in items[0],
          "survey: an item whose patch the local tree already carries is marked present")
    check("merge=yes" in items[2] and "files=c.py" in items[2],
          "survey: a merge item lists the files it changed against its first parent")
    check("LICENSE=MIT REUSE=yes" in out, f"survey: a permissive licence allows reuse ({out!r})")
    code, out = _cli(["survey", "--against", head_before + "~1", "--repo", str(local)])
    check(code == 0 and "present=no" in [l for l in out.splitlines() if l.startswith("ITEM 2 ")][0],
          f"survey --against: presence is judged against the named ref, not HEAD ({out!r})")
    check(_git(local, "rev-parse", "HEAD") == head_before and _git(local, "status", "--porcelain") == "",
          "survey: changes nothing in the local tree")
    check(ledger.read_state(local)[0].synced_sha == base, "survey: the watermark is untouched")

    code, out = _cli(["advance", feat_b, "--outcome", "merged", "--repo", str(local)])
    check(code == 1 and "WATERMARK=held" in out and ledger.read_state(local)[0].synced_sha == base,
          f"advance: a merged item past an unfinished one holds the watermark and says so ({out!r})")
    code, out = _cli(["advance", feat_a, "--outcome", "merged", "--repo", str(local)])
    check(code == 0 and ledger.read_state(local)[0].synced_sha == feat_a, f"advance: the next item moves it ({out!r})")
    code, out = _cli(["advance", merge_c, "--outcome", "skipped", "--repo", str(local)])
    check(code == 2, f"advance: a skip without a reason is refused ({out!r})")
    code, out = _cli(["advance", merge_c, "--outcome", "skipped", "--reason", "not wanted here", "--repo", str(local)])
    st = ledger.read_state(local)[0]
    check(code == 1 and st.synced_sha == feat_a and [s.sha for s in st.skipped] == [merge_c],
          f"advance: a skip past a gap is recorded in the ledger but the watermark holds ({out!r})")
    code, out = _cli(["advance", feat_b, "--outcome", "skipped", "--reason", "already present", "--repo", str(local)])
    st = ledger.read_state(local)[0]
    check(code == 0 and st.synced_sha == merge_c, f"advance: closing the gap walks over the recorded skip ({out!r})")
    code, out = _cli(["advance", "0" * 40, "--outcome", "merged", "--repo", str(local)])
    check(code == 2, f"advance: a sha that is not a pending source item is refused ({out!r})")

    _git(local, "add", "-A")
    _git(local, "commit", "-q", "-m", "watermark")
    code, out = _cli(["status", "--ref", "HEAD", "--repo", str(local)])
    check(code == 0 and f"SYNCED_SHA={merge_c}" in out and "SKIPPED=2" in out,
          f"status --ref: reads the watermark committed on a ref ({out!r})")

    check(source.classify_licence("") == ("none", "no"), "licence: no licence file refuses reuse")
    check(source.classify_licence("GNU GENERAL PUBLIC LICENSE\nVersion 3")[1] == "copyleft",
          "licence: a copyleft licence is its own state, never a plain yes")
    check(source.classify_licence("All rights reserved. Do whatever.")[1] == "unknown",
          "licence: an unrecognised licence is unknown, never yes")
    check(source.classify_licence("Apache License\n Version 2.0, January 2004") == ("Apache-2.0", "yes"),
          "licence: Apache-2.0 allows reuse")
finally:
    shutil.rmtree(root, ignore_errors=True)

_h.report_and_exit("test_upstream_sync")
