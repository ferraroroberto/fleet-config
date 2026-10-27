"""Unit tests for skills/_lib/vendored_wave/ignore_block.py: the generated `.ignore` block.

Proves the block is built from the manifest's dest paths, that hand-written lines survive a
regeneration, that a stale, missing or malformed block is reported as its own state, that
`vendored_drift.scan_fleet` and `plan.plan_wave` carry that state to the wave, and that
this repo's own `.ignore` matches its own manifest. Throwaway repos only.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_vendored_ignore.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import vendored_drift as vd  # noqa: E402
from vendored_wave import ignore_block as ib  # noqa: E402
from vendored_wave import plan as vw  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

DESTS = ["web/static/_vendored/nav", "scripts/gate.py"]
PATTERNS = ["/scripts/gate.py", "/web/static/_vendored/nav"]

# ---- patterns ---------------------------------------------------------------
check(ib.dest_patterns(DESTS) == PATTERNS, "dest_patterns: anchored at the repo root, sorted, no trailing slash")
check(ib.dest_patterns(["./a\\b/", "/a/b", "a/b"]) == ["/a/b"],
      "dest_patterns: backslashes, a leading ./ or / and a trailing / normalise to one entry")
for bad in ("../x", "a/../b", "", ".", "a/*.py", "a/#b", "!a"):
    try:
        ib.dest_patterns([bad])
        check(False, f"dest_patterns: {bad!r} must be refused")
    except ValueError:
        check(True, f"dest_patterns: {bad!r} is refused rather than turned into a pattern")

# ---- apply_block ------------------------------------------------------------
created = "\n".join([ib.BEGIN, *PATTERNS, ib.END]) + "\n"
check(ib.apply_block("", PATTERNS) == created, "apply_block: an empty file becomes just the block")
hand = "# mine\nnode_modules\n"
added = ib.apply_block(hand, PATTERNS)
check(added.startswith(hand + "\n" + ib.BEGIN), "apply_block: the block is appended after the hand-written lines")
check(ib.apply_block(added, PATTERNS) == added, "apply_block: regenerating an up-to-date file changes nothing")
changed = ib.apply_block(added, ["/only/one"])
check(changed.startswith(hand) and "/only/one" in changed and "/scripts/gate.py" not in changed,
      "apply_block: a changed manifest rewrites the block and keeps the lines before it")
below = ib.apply_block(added + "after_block.tmp\n", PATTERNS[:1])
check(below.endswith(ib.END + "\nafter_block.tmp\n") and "# mine" in below,
      "apply_block: hand-written lines on either side of the block both survive")
check(ib.apply_block(added, []) == hand, "apply_block: an emptied manifest removes the block and its blank line")
check(ib.apply_block(hand, []) == hand, "apply_block: no block and no patterns leaves the file alone")
crlf = ib.apply_block("a\r\n", PATTERNS)
check(crlf.count("\r\n") == crlf.count("\n"), "apply_block: a CRLF file stays CRLF throughout")

# ---- state ------------------------------------------------------------------
check(ib.block_state(None, []) == "ok" and ib.block_state("", []) == "ok", "block_state: nothing to ignore and no file is ok")
check(ib.block_state(None, PATTERNS) == "missing" and ib.block_state(hand, PATTERNS) == "missing",
      "block_state: patterns but no block is missing")
check(ib.block_state(added, PATTERNS) == "ok", "block_state: a current block is ok")
check(ib.block_state(added, PATTERNS[:1]) == "stale", "block_state: a block that disagrees with the manifest is stale")
check(ib.block_state(added, []) == "stale", "block_state: a block for an empty manifest is stale")
check(ib.block_state(ib.BEGIN + "\n/x\n", PATTERNS) == "malformed", "block_state: a lone BEGIN marker is malformed")
check(ib.block_state(added + added, PATTERNS) == "malformed", "block_state: two blocks is malformed")
check(ib.block_state(ib.END + "\n" + ib.BEGIN + "\n", PATTERNS) == "malformed", "block_state: END before BEGIN is malformed")

root = Path(tempfile.mkdtemp(prefix="vendored_ignore_"))
try:
    def repo(name: str, manifest_dests: list, ignore_text=None) -> Path:
        d = root / name
        d.mkdir()
        rows = "".join(f'c{i} = {{ src = "{x}", sha = "s", dest = "{x}" }}\n' for i, x in enumerate(manifest_dests))
        (d / ".fleet.toml").write_text('layer = "x"\n' + (f"[vendored]\n{rows}" if rows else ""), encoding="utf-8")
        if ignore_text is not None:
            (d / ".ignore").write_text(ignore_text, encoding="utf-8", newline="")
        return d

    # ---- write / check CLI over a repo ----
    r = repo("fresh", DESTS)
    check(ib.main(["check", str(r)]) == 1, "check: a repo with a manifest and no .ignore exits 1 (missing)")
    check(ib.main(["write", str(r)]) == 0 and (r / ".ignore").read_text(encoding="utf-8") == created,
          "write: creates .ignore with the block")
    check(ib.main(["check", str(r)]) == 0, "check: exits 0 once written")
    check(ib.write_repo(r, DESTS) == "unchanged", "write_repo: a second write changes nothing")
    r2 = repo("hand", DESTS, hand)
    ib.write_repo(r2, DESTS)
    check((r2 / ".ignore").read_text(encoding="utf-8") == added, "write: keeps the hand-written lines and appends the block")
    r3 = repo("emptied", [], created)
    check(ib.write_repo(r3, []) == "removed" and not (r3 / ".ignore").exists(),
          "write_repo: a manifest with no vendored entries removes a file that held only the block")
    r4 = repo("bad", DESTS, ib.BEGIN + "\n")
    check(ib.main(["write", str(r4)]) == 3 and (r4 / ".ignore").read_text(encoding="utf-8") == ib.BEGIN + "\n",
          "write: a malformed block exits 3 and the file is not touched")
    check(ib.main(["check", str(root / "nowhere")]) == 3, "check: an unreadable manifest is not established (exit 3), never ok")

    # ---- the scan and the wave plan carry the state ----
    scaffold = root / "project-scaffolding"
    scaffold.mkdir()
    repos = {"project-scaffolding": scaffold, "fresh": r, "hand": r2, "bare": repo("bare", DESTS),
             "stale": repo("stale", DESTS, ib.apply_block("", PATTERNS[:1])), "broken": r4}
    report = vd.scan_fleet(scaffold, repos=repos)
    check(report["ignore_blocks"] == {"bare": "missing", "broken": "malformed", "fresh": "ok", "hand": "ok", "stale": "stale"},
          f"scan: every adopter's .ignore state is reported ({report['ignore_blocks']})")
    check(report["coverage"]["ignore_drift"] == ["bare", "broken", "stale"],
          "scan: coverage names every repo whose block is not ok")
    plan = vw.plan_wave(report)
    check([e["repo"] for e in plan["ignore_only"]] == ["bare", "stale"],
          "plan: a repo behind on nothing but with a missing or stale block gets its own entry")
    check([h["repo"] for h in plan["held_ignore"]] == ["broken"], "plan: a malformed block is held for a human, not rewritten")
    check(plan["prs"] == 2 and "HUMAN CALL (.ignore block malformed): broken" in vw.render_text(plan),
          "plan: ignore-only entries count as PRs and the held repo is named in the dry-run text")
    body = plan["ignore_only"][0]["pr_body"]
    check("Part of project-scaffolding@" in body and "Closes" not in body and "\n\n" in body,
          "plan: the ignore-only PR body links Part of and carries no closing keyword")

    # ---- this repo's own block matches its own manifest ----
    own = ib.repo_state(REPO, ib.manifest_dests(REPO))
    check(own == "ok", f"this repo: .ignore matches the [vendored] manifest ({own}); run ignore_block.py write .")
finally:
    shutil.rmtree(root, ignore_errors=True)


_h.report_and_exit("test_vendored_ignore")
