"""The /downstream-sync half of the shared `[upstream]` helper.

Drives skills/_lib/upstream_sync against throwaway git repos: a source repo and a local
copy that built features of its own. Proves that a table written before `offered` existed
still reads and rewrites byte for byte (so /upstream-sync keeps working on it), that
`compare` lists the local-only work and tells apart what came in through a forward sync,
what the source already carries and what was already offered, changing nothing, that
`offer` records a handed-back feature and refuses anything else, that a spec carrying
code, private paths, hosts, emails or the local repo's name is refused, and that a
forward survey marks a source item answering an offered feature as superseding the local
one, which `advance --supersedes` then retires. No real repo is touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_downstream_sync.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402
from upstream_sync import cli, ledger  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"

GOOD_SPEC = """# Export the report as CSV

## Problem
Readers want the weekly report in a spreadsheet.

## Behaviour
A download button on the report page returns the same rows as a CSV file.

## Interface
A `GET /report.csv` route; no new config.

## Acceptance criteria
- [ ] The CSV has one row per report row and a header line.

## Tests
- A request to `/report.csv` returns status 200 and the header line.
"""


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


def _configure(repo: Path) -> None:
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
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


def _local_lines(out: str) -> dict:
    return {line.split("sha=")[1].split()[0]: line for line in out.splitlines() if line.startswith("LOCAL ")}


root = Path(tempfile.mkdtemp(prefix="downstream-sync-"))
try:
    # --- the table: old shape stays readable and byte-stable --------------------------

    old = root / "old"
    old.mkdir()
    old_text = ('layer = "working-pipe"\n\n[upstream]\n'
                '# Written by /upstream-sync and /downstream-sync (skills/_lib/upstream_sync); never by hand.\n'
                'url = "https://example.invalid/src.git"\nbranch = "main"\n'
                f'synced_sha = "{"a" * 40}"\n\n[[upstream.skipped]]\nsha = "{"b" * 40}"\nreason = "not here"\n')
    (old / ".fleet.toml").write_text(old_text, encoding="utf-8")
    state, status = ledger.read_state(old)
    check(status == "ok" and state.offered == [] and len(state.skipped) == 1,
          f"compat: a table written before `offered` existed still reads ({status}, {state})")
    ledger.write_state(old, state)
    check((old / ".fleet.toml").read_text(encoding="utf-8") == old_text,
          "compat: rewriting a table with nothing offered is byte-identical to the old format")

    state.offered.append(ledger.Offer(local_sha="c" * 40, feature='CSV "export"', source_issue=7))
    state.offered.append(ledger.Offer(local_sha="d" * 40, feature="handed over by hand"))
    ledger.write_state(old, state)
    back, status = ledger.read_state(old)
    check(status == "ok" and back == state, f"offered: the list round-trips, with and without a source issue ({back})")
    (old / ".fleet.toml").write_text(old_text + '\n[[upstream.offered]]\nfeature = "no sha"\n', encoding="utf-8")
    check(ledger.read_state(old)[1] == "invalid", "offered: an entry missing its local sha makes the table invalid")

    # --- compare: the local-only work -------------------------------------------------

    src = root / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    _configure(src)
    _commit(src, "LICENSE", MIT, "init")
    _commit(src, "core.py", "x = 1\n", "core (#1)")

    local = root / "localcopy"
    _git(root, "clone", "-q", str(src), str(local))
    _configure(local)
    _git(local, "remote", "remove", "origin")
    card = _commit(local, ".fleet.toml", 'layer = "working-pipe"\n', "local card")
    code, out = _cli(["init", str(src), "--repo", str(local)])
    _git(local, "commit", "-q", "-am", "set watermark")
    init_commit = _git(local, "rev-parse", "HEAD")

    feat_x = _commit(local, "x.py", "x = 2\n", "feat: csv export (#10)")
    # A forward port: the source merges `a`, the local lane brings it in with its watermark.
    feat_a = _commit(src, "a.py", "a = 1\n", "feat: a (#2)")
    _cli(["survey", "--repo", str(local)])
    (local / "a.py").write_text("a = 1\n", encoding="utf-8")
    _cli(["advance", feat_a, "--outcome", "merged", "--repo", str(local)])
    _git(local, "add", "-A")
    _git(local, "commit", "-q", "-m", "feat: a here (#11)")
    port = _git(local, "rev-parse", "HEAD")
    # The same patch built locally before the source merged it too.
    feat_b = _commit(local, "b.py", "b = 1\n", "feat: b locally (#12)")
    _commit(src, "b.py", "b = 1\n", "feat: b (#3)")
    _git(local, "fetch", "-q", "upstream-sync")
    feat_y = _commit(local, "y.py", "y = 1\n", "feat: dark mode (#13)")

    head_before = _git(local, "rev-parse", "HEAD")
    code, out = _cli(["compare", "--repo", str(local)])
    lines = _local_lines(out)
    check(code == 0 and list(lines) == [card, init_commit, feat_x, port, feat_b, feat_y],
          f"compare: lists every local-only first-parent commit, oldest first ({out!r})")
    check("synced=yes" in lines[port] and "synced=yes" in lines[init_commit] and "synced=no" in lines[feat_x],
          "compare: a commit that moved the [upstream] table came in through a sync, never a local feature")
    check("present=yes" in lines[feat_b] and "present=no" in lines[feat_y],
          "compare: a local commit whose patch the source already carries is marked present")
    check("candidate=yes" in lines[feat_x] and "candidate=yes" in lines[feat_y]
          and "candidate=no" in lines[port] and "candidate=no" in lines[feat_b],
          "compare: only local work that is neither synced, present nor offered is a candidate")
    check("pr=10" in lines[feat_x] and "files=x.py" in lines[feat_x], "compare: each line names its PR and files")
    check(_git(local, "rev-parse", "HEAD") == head_before and _git(local, "status", "--porcelain") == "",
          "compare: changes nothing in the local tree")
    code, out = _cli(["compare", "--since", feat_x, "--repo", str(local)])
    check(list(_local_lines(out)) == [port, feat_b, feat_y], f"compare --since: starts after the named commit ({out!r})")

    # --- offer -----------------------------------------------------------------------

    code, out = _cli(["offer", feat_x, "--feature", "CSV export", "--source-issue", "7", "--repo", str(local)])
    st = ledger.read_state(local)[0]
    check(code == 0 and [o.local_sha for o in st.offered] == [feat_x] and st.offered[0].source_issue == 7,
          f"offer: records the handed-back feature with its source issue ({out!r})")
    code, out = _cli(["offer", feat_x, "--feature", "again", "--repo", str(local)])
    check(code == 2, f"offer: offering the same commit twice is refused ({out!r})")
    code, out = _cli(["offer", port, "--feature", "ported", "--repo", str(local)])
    check(code == 2, f"offer: a commit that came in through a sync is refused ({out!r})")
    code, out = _cli(["offer", "0" * 40, "--feature", "ghost", "--repo", str(local)])
    check(code == 2, f"offer: a sha that is not local work is refused ({out!r})")
    code, out = _cli(["offer", feat_y, "--feature", "  ", "--repo", str(local)])
    check(code == 2, f"offer: an offer needs a feature name ({out!r})")
    code, out = _cli(["compare", "--repo", str(local)])
    lines = _local_lines(out)
    check("offered=yes" in lines[feat_x] and "candidate=no" in lines[feat_x] and "candidate=yes" in lines[feat_y],
          f"compare: an offered feature is no longer a candidate ({out!r})")
    _git(local, "add", "-A")
    _git(local, "commit", "-q", "-m", "record offer")

    # --- the spec lint ---------------------------------------------------------------

    spec = root / "spec.md"

    def lint(text: str, *extra: str) -> tuple:
        spec.write_text(text, encoding="utf-8")
        return _cli(["lint-spec", str(spec), "--repo", str(local), *extra])

    code, out = lint(GOOD_SPEC)
    check(code == 0 and "SPEC=ok" in out, f"lint-spec: a complete spec with no private detail passes ({out!r})")
    fenced = GOOD_SPEC + "\n```python\nprint(1)\n```\n"
    code, out = lint(fenced)
    check(code == 2 and "code" in out, f"lint-spec: a code block is refused by default ({out!r})")
    code, out = lint(fenced, "--allow-code")
    check(code == 0, f"lint-spec --allow-code: a code block passes when the source may see code ({out!r})")
    for bad, why in ((r"Stored under C:\Users\someone\data.", "a Windows path"),
                     ("Saved to /home/someone/data.", "a home path"),
                     ("Served from box.tail1234.ts.net:8445.", "a private host"),
                     ("Calls http://127.0.0.1:8000/v1.", "a loopback address"),
                     ("Ask someone@example.com.", "an email"),
                     ("Set token ghp_abcdefghijklmnopqrstuvwxyz0123456789.", "a secret-shaped token"),
                     ("Same as in localcopy.", "the local repo's name")):
        code, out = lint(GOOD_SPEC + "\n" + bad + "\n")
        check(code == 2, f"lint-spec: refuses {why} ({out!r})")
    code, out = lint(GOOD_SPEC.replace("## Tests", "## Notes"))
    check(code == 2 and "Tests" in out, f"lint-spec: a spec missing a required section is refused ({out!r})")
    code, out = lint(GOOD_SPEC + "\nAlso mention Acme.\n", "--forbid", "acme")
    check(code == 2, f"lint-spec --forbid: a named word is refused, case-insensitively ({out!r})")

    # --- the return: a source item answering an offer supersedes the local one -------

    answer = _commit(src, "x.py", "x = 3\n", "feat: export rows as CSV (#4)\n\nCloses #7")
    code, out = _cli(["survey", "--repo", str(local)])
    items = [line for line in out.splitlines() if line.startswith("ITEM ")]
    check(code == 0 and any(f"sha={answer}" in i and f"supersedes={feat_x}" in i for i in items),
          f"survey: a source item closing an offered feature's issue supersedes the local commit ({out!r})")
    check(all("supersedes=-" in i for i in items if f"sha={answer}" not in i),
          "survey: an unrelated source item supersedes nothing")
    check(any(line.startswith("OFFERED ") and f"local_sha={feat_x}" in line for line in out.splitlines()),
          "survey: lists the open offers so the behaviour match can be judged too")
    code, out = _cli(["advance", answer, "--outcome", "merged", "--supersedes", "0" * 40, "--repo", str(local)])
    check(code == 2 and len(ledger.read_state(local)[0].offered) == 1,
          f"advance --supersedes: a sha that was never offered is refused, changing nothing ({out!r})")
    pending = [i.split("sha=")[1].split()[0] for i in items]
    for sha in pending[:-1]:
        _cli(["advance", sha, "--outcome", "skipped", "--reason", "already present", "--repo", str(local)])
    code, out = _cli(["advance", answer, "--outcome", "merged", "--supersedes", feat_x, "--repo", str(local)])
    st = ledger.read_state(local)[0]
    check(code == 0 and st.offered == [] and st.synced_sha == answer,
          f"advance --supersedes: the source's version retires the offer and moves the watermark ({out!r})")
finally:
    shutil.rmtree(root, ignore_errors=True)

_h.report_and_exit("test_downstream_sync")
