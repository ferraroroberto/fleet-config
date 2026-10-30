"""Tests for the `.proof/` store in skills/_lib/ui_proof, against a real temp repo and worktree.

Covers capture-before/after into the primary checkout's `.proof/` from a
worktree; `build` with and without a layout_lint manifest (lint result vs
"not run"); the self-contained page with before/after per criterion and a
no-visual line; `index.json` keyed by PR; the finish refusal without the page
and on a tracked or unignored `.proof/`; and `prune` removing only folders
older than the threshold. Also asserts this repo ignores `.proof/`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_ui_proof_store.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402
from ui_proof import cli, store  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

_h = CheckHarness()
check = _h.check

CLAUDE_MD = """\
# Project

## UX surface
- design spec applies: yes
- paths:
  - app/static/**
- key views:
  - /
"""
PNG = b"\x89PNG\r\n\x1a\nfake"
LEGS = (("phone", "light"), ("phone", "dark"))


def git(repo: Path, *args: str) -> None:
    res = run_git(["-C", str(repo), *args])
    assert res.returncode == 0, f"git {args}: {res.stderr}"


def commit_all(repo: Path, msg: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def run(*argv: str):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    out = dict(line.split("=", 1) for line in buf.getvalue().splitlines() if "=" in line)
    return code, out, buf.getvalue()


def shots(folder: Path, tag: str, state: str = "home.sheet") -> list:
    args = []
    for viewport, theme in LEGS:
        png = folder / f"{tag}-{viewport}-{theme}.png"
        png.write_bytes(PNG + tag.encode())
        args += ["--image", f"{state}:{viewport}-{theme}={png}"]
    return args


def manifest(folder: Path, findings=()) -> Path:
    entries = []
    for viewport, theme in LEGS:
        png = folder / f"m-{viewport}-{theme}.png"
        png.write_bytes(PNG)
        entries.append({"state": "home.sheet", "viewport": viewport, "theme": theme,
                        "screenshot": str(png), "findings": list(findings)})
    path = folder / "manifest.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


# This repo ignores `.proof/` and tracks nothing under it.
check(store.guard_lines(REPO) == [], f"fleet-config ignores .proof/ — got {store.guard_lines(REPO)}")

scratch = Path(tempfile.mkdtemp(prefix="ui_proof_shots_"))
repo = init_repo(empty_commit=False)
wt = Path(tempfile.mkdtemp(prefix="ui_proof_wt_")) / "wt"
try:
    (repo / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")
    (repo / ".gitignore").write_text(".proof/\n", encoding="utf-8")
    (repo / "app" / "static").mkdir(parents=True)
    (repo / "app" / "static" / "view.css").write_text("a{}\n", encoding="utf-8")
    commit_all(repo, "base")
    git(repo, "worktree", "add", "-q", "-b", "feat/7-sheet", str(wt))
    (wt / "app" / "static" / "view.css").write_text("a{color:red}\n", encoding="utf-8")
    commit_all(wt, "view")
    root, primary = wt / ".proof", repo / ".proof"

    # Captures from a worktree land in the worktree's own .proof/, staged by branch; the primary is untouched.
    code, out, raw = run("capture-before", str(wt), *shots(scratch, "before"))
    staging = root / "_branch" / "feat-7-sheet"
    check(code == 0 and (staging / "before-home.sheet-phone-light.png").is_file(),
          f"capture-before stages into the worktree's .proof/_branch — got {raw!r}")
    check(not primary.exists(), "a worktree capture does not create the primary's .proof/")
    code, _, raw = run("capture-after", str(wt), *shots(scratch, "after"))
    check(code == 0 and (staging / "after-home.sheet-phone-dark.png").is_file(), f"capture-after stages — {raw!r}")
    code, _, raw = run("capture-after", str(wt), "--image", "bad-format")
    check(code == 2 and "CAPTURED=none" in raw, "a malformed --image is refused")

    # The finish gate refuses a UI diff without the page, vendored or not.
    code, out, _ = run("check", str(wt), "--base", "main", "--pr", "7")
    check(code == 1 and out["PROOF_PAGE"] == "missing" and ".proof/7/index.html" in out["NEED"],
          f"no page refuses, layout_lint not vendored — got {out} exit {code}")

    # Build without a manifest: lint "not run", before/after per criterion, no-visual line.
    code, out, raw = run("build", str(wt), "--pr", "7", "--criterion", "home.sheet=Sheet title wraps; open the sheet",
                         "--no-visual", "Logs keep their level", "--title", "Sheet fix", "--issue", "6")
    page_path = root / "7" / "index.html"
    check(code == 0 and Path(out["PROOF_PAGE"]) == page_path and not staging.exists(),
          f"build moves staging into .proof/7 — got {raw!r}")
    page = page_path.read_text(encoding="utf-8")
    check(page.count("data:image/png;base64,") == 4 and "Sheet title wraps; open the sheet" in page
          and page.count('class="pair"') == 2 and "Logs keep their level" in page and "No visual change." in page,
          "the page pairs before/after per leg under the criterion line and lists the no-visual criterion")
    check("Layout lint: not run" in page and "not merged yet" in page, "no manifest -> lint not run; unmerged")
    check("<script" not in page and "src=\"http" not in page, "the page is self-contained")
    check(Path(out["SEND_PATH"]).is_file() and Path(out["SEND_PATH"]).parts[-3:-1] == ("claude", "ui-proof"),
          "a sendable copy sits under %TEMP%/claude/ui-proof")
    code, out, _ = run("check", str(wt), "--base", "main", "--pr", "7")
    check(code == 0 and out["PROOF_PAGE"] == "present" and out["VISUAL_PROOF"] == "fresh" and out["NEED"] == "",
          f"a built page without a manifest reads fresh and asks for nothing — got {out}")
    check("layout lint not run" in out["DETAIL"], f"the unrun lint is reported, not hidden — got {out['DETAIL']!r}")
    check(not primary.exists(), "build leaves the primary's .proof/ alone until the merge")

    # A rebuild after the merge stamps the sha, keeps criteria, title and issue, and copies into the primary.
    code, out, raw = run("build", str(wt), "--pr", "7", "--sha", "abc1234")
    check(out.get("PUBLISHED") == str(primary / "7") and (primary / "7" / "index.html").is_file(),
          f"build --sha publishes the PR folder to the primary — got {raw!r}")
    check(json.loads((primary / "index.json").read_text(encoding="utf-8"))["7"]["merge_sha"] == "abc1234",
          "the primary's index row carries the merge sha")
    stale = primary / "7" / "stale.txt"
    stale.write_text("x", encoding="utf-8")
    run("build", str(wt), "--pr", "7", "--sha", "abc1234")
    check(not stale.exists() and (primary / "7" / "index.html").is_file(),
          "publishing again replaces the primary's folder whole")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    row = index["7"]
    check(row["merge_sha"] == "abc1234" and row["title"] == "Sheet fix" and row["issue"] == 6
          and row["branch"] == "feat/7-sheet" and row["utc"], f"index.json keyed by PR — got {row}")
    page = page_path.read_text(encoding="utf-8")
    check("abc1234" in page and "Sheet title wraps" in page, "the rebuilt page carries the sha and the criterion")

    # Build with a manifest: the lint result is shown and page text never is.
    bad = {"kind": "overflow", "severity": "error", "element": "div.sheet", "detail": "412px > 358px",
           "text": "SECRET PAGE TEXT", "allowed": None}
    run("capture-after", str(wt), "--manifest", str(manifest(scratch, [bad])))
    code, out, _ = run("build", str(wt), "--pr", "7")
    page = page_path.read_text(encoding="utf-8")
    check("Layout lint errors" in page and "overflow div.sheet" in page and "SECRET PAGE TEXT" not in page,
          "a manifest's lint error is listed without page text")
    code, out, _ = run("check", str(wt), "--base", "main", "--pr", "7")
    check(out["VISUAL_PROOF"].startswith("failing") and "overflow div.sheet" in out["NEED"],
          f"a page whose captures carry a lint error reads failing — got {out}")
    run("capture-after", str(wt), "--manifest", str(manifest(scratch)))
    run("build", str(wt), "--pr", "7")
    check("Layout lint: clean" in page_path.read_text(encoding="utf-8"), "a clean manifest reads clean")
    code, out, _ = run("check", str(wt), "--base", "main", "--pr", "7")
    check(out["VISUAL_PROOF"] == "fresh" and "layout lint clean" in out["DETAIL"], f"a clean page reads fresh — got {out}")

    # Several criteria on one state share its before/after pairs instead of collapsing into one.
    run("build", str(wt), "--pr", "7", "--criterion", "home.sheet=Title wraps", "--criterion", "home.sheet=Close stays reachable")
    page = page_path.read_text(encoding="utf-8")
    check("Title wraps" in page and "Close stays reachable" in page and page.count('class="pair"') == 2,
          "two criteria on one state both appear, over one set of pairs")

    # Nothing in the tree: .proof/ is ignored.
    status = run_git(["-C", str(repo), "status", "--porcelain"]).stdout.strip()
    check(status == "", f"the primary tree stays clean — got {status!r}")

    # A tracked or unignored .proof/ refuses.
    (wt / ".gitignore").write_text("", encoding="utf-8")
    code, out, _ = run("check", str(wt), "--base", "main", "--pr", "7")
    check(code == 1 and "not gitignored" in out["NEED"], f"an unignored .proof/ refuses — got {out}")
    (wt / ".proof").mkdir(exist_ok=True)
    (wt / ".proof" / "leak.png").write_bytes(PNG)
    commit_all(wt, "leak")
    code, out, _ = run("check", str(wt), "--base", "main", "--pr", "7")
    check(code == 1 and "is tracked (" in out["NEED"] and "git rm -r --cached" in out["NEED"], f"a tracked .proof/ refuses — got {out}")

    # Prune removes only folders older than the threshold.
    (root / "8").mkdir()
    (root / "8" / "index.html").write_text("x", encoding="utf-8")
    (root / "notes").mkdir()
    old = time.time() - 40 * 86400
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    index["8"] = {"utc": "2000-01-01T00:00:00Z"}
    (root / "index.json").write_text(json.dumps(index), encoding="utf-8")
    for d in (root / "8", root / "notes"):
        os.utime(d, (old, old))
    code, out, raw = run("prune", str(wt), "--older-than", "30d")
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    check(out["PRUNED"] == "1" and not (root / "8").exists() and "8" not in index,
          f"the old PR folder and its row go — got {raw!r}")
    check((root / "7" / "index.html").is_file() and "7" in index and (root / "notes").is_dir(),
          "a young PR folder and a non-PR folder stay")
    code, out, _ = run("prune", str(wt), "--older-than", "soon")
    check(code == 2 and out["PRUNED"].startswith("0"), "a malformed threshold is refused")
finally:
    run_git(["-C", str(repo), "worktree", "remove", "--force", str(wt)])
    for path in (repo, scratch, wt.parent):
        shutil.rmtree(path, ignore_errors=True)


_h.report_and_exit("ui_proof_store")
