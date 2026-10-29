"""Tests for skills/_lib/ui_proof (fleet-config#1707), against a real temp repo.

Covers the finish gate's states: no UX surface is `n/a`; a UI diff with no
proof is `missing` and names what to capture, refusing only where
`layout_lint` is vendored; missing legs, lint errors, an edited tree or an
edited manifest each report their own state; an allowed finding passes; a
failed diff names the key views; and the walkthrough is self-contained,
indexed by `<repo>#<PR>` and written outside the tree.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_ui_proof.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402
from ui_proof import cli, proof, walkthrough  # noqa: E402

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
  - app/static/**/*.css
- key views:
  - /          (home)
  - /settings
"""
VENDORED = "[vendored]\nlayout_lint = { src = \"tests/e2e/_layout_lint.py\", sha = \"x\" }\n"
PNG = b"\x89PNG\r\n\x1a\nfake"


def git(repo: Path, *args: str) -> None:
    res = run_git(["-C", str(repo), *args])
    assert res.returncode == 0, f"git {args}: {res.stderr}"


def commit_all(repo: Path, msg: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def write_manifest(folder: Path, states, legs=proof.REQUIRED_LEGS, findings=()) -> Path:
    entries = []
    for state in states:
        for viewport, theme in legs:
            shot = folder / f"{state}-{viewport}-{theme}.png"
            shot.write_bytes(PNG)
            entries.append({"state": state, "viewport": viewport, "theme": theme,
                            "screenshot": str(shot), "ms": 5, "findings": list(findings)})
    path = folder / "manifest.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


def run_check(repo: Path, base: str = "main"):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["check", str(repo), "--base", base])
    out = dict(line.split("=", 1) for line in buf.getvalue().splitlines() if "=" in line)
    return code, out, buf.getvalue()


proof_root = Path(tempfile.mkdtemp(prefix="ui_proof_out_"))
store = Path(tempfile.mkdtemp(prefix="ui_proof_store_"))
repo = init_repo(empty_commit=False)
try:
    (repo / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")
    (repo / "app" / "static").mkdir(parents=True)
    (repo / "app" / "static" / "view.js").write_text("// v1\n", encoding="utf-8")
    commit_all(repo, "base")
    git(repo, "checkout", "-q", "-b", "feat/x")

    # A backend-only diff needs no proof.
    (repo / "server.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(repo, "backend")
    code, out, _ = run_check(repo)
    check(out["VISUAL_PROOF"] == "n/a" and out["TOUCHED"] == "no" and code == 0,
          f"backend-only diff is n/a — got {out}")

    # View JS alone is a UI diff, even though the block names only CSS.
    (repo / "app" / "static" / "view.js").write_text("// v2\n", encoding="utf-8")
    commit_all(repo, "view")
    code, out, raw = run_check(repo)
    check(out["TOUCHED"] == "yes", f"view JS counts as UI — got {out}")
    check(raw.splitlines()[0] == "VISUAL_PROOF=missing (layout_lint not vendored)" and code == 0,
          f"unvendored repo: loud advisory line, exit 0 — got {raw!r} exit {code}")
    check("no proof manifest recorded" in out["NEED"] and "capture_states" in out["NEED"],
          f"the missing proof is named with how to capture it — got {out['NEED']!r}")

    # Vendoring layout_lint switches the refusal on.
    (repo / ".fleet.toml").write_text(VENDORED, encoding="utf-8")
    commit_all(repo, "vendor")
    code, out, _ = run_check(repo)
    check(out["VISUAL_PROOF"] == "missing" and out["ENFORCED"] == "yes" and code == 1,
          f"vendored repo refuses a UI diff without proof — got {out} exit {code}")

    # Missing legs are named one by one.
    m = write_manifest(proof_root, ["home.sheet"], legs=proof.REQUIRED_LEGS[:2])
    with contextlib.redirect_stdout(io.StringIO()):
        cli.main(["record", str(repo), "--manifest", str(m), "--criterion", "home.sheet=Sheet fits the phone"])
    code, out, _ = run_check(repo)
    check(out["VISUAL_PROOF"] == "missing" and "home.sheet desktop-light" in out["NEED"]
          and "home.sheet desktop-dark" in out["NEED"] and code == 1,
          f"missing legs are named — got {out}")

    # A failing finding blocks and is quoted without page text.
    bad = {"kind": "overflow", "severity": "error", "element": "div.sheet > span", "other": None,
           "detail": "content 412px > box 358px", "text": "SECRET PAGE TEXT", "allowed": None}
    m = write_manifest(proof_root, ["home.sheet"], findings=[bad])
    with contextlib.redirect_stdout(io.StringIO()):
        cli.main(["record", str(repo), "--manifest", str(m), "--criterion", "home.sheet=Sheet fits the phone"])
    code, out, _ = run_check(repo)
    check(out["VISUAL_PROOF"] == "failing" and "overflow div.sheet > span" in out["NEED"] and code == 1,
          f"a lint error is failing and named — got {out}")
    check("SECRET PAGE TEXT" not in out["NEED"], "the failing line never quotes page text")

    # An allowed finding and a warning pass.
    allowed = dict(bad, allowed="framework chrome")
    warn = dict(bad, kind="contrast", severity="warning")
    m = write_manifest(proof_root, ["home.sheet"], findings=[allowed, warn])
    with contextlib.redirect_stdout(io.StringIO()):
        cli.main(["record", str(repo), "--manifest", str(m), "--criterion", "home.sheet=Sheet fits the phone"])
    code, out, _ = run_check(repo)
    check(out["VISUAL_PROOF"] == "fresh" and code == 0, f"clean proof is fresh — got {out}")

    # Editing the tree, or the manifest, makes the proof stale.
    (repo / "app" / "static" / "view.js").write_text("// v3\n", encoding="utf-8")
    code, out, _ = run_check(repo)
    check(out["VISUAL_PROOF"] == "stale" and "app/static/view.js" in out["NEED"] and code == 1,
          f"an edit after record is stale and names the path — got {out}")
    git(repo, "checkout", "--", "app/static/view.js")
    check(run_check(repo)[1]["VISUAL_PROOF"] == "fresh", "restoring the tree makes it fresh again")
    m.write_text(m.read_text(encoding="utf-8").replace('"ms": 5', '"ms": 6'), encoding="utf-8")
    check(run_check(repo)[1]["VISUAL_PROOF"] == "stale", "an edited manifest is stale")

    # A diff that cannot be taken names every key view.
    code, out, _ = run_check(repo, base="no-such-ref")
    check(out["TOUCHED"] == "unknown" and "/,/settings" in out["NEED"] and code == 1,
          f"TOUCHED=unknown names the key views — got {out}")
    with contextlib.redirect_stdout(io.StringIO()):
        cli.main(["record", str(repo), "--manifest", str(m), "--criterion", "home.sheet=Sheet fits the phone"])
    code, out, _ = run_check(repo, base="no-such-ref")
    check(out["VISUAL_PROOF"] == "fresh" and out["NEED"] == "" and "/,/settings" in out["DETAIL"] and code == 0,
          f"fresh proof on an unknown diff passes with an empty NEED — got {out}")

    # A malformed criterion is refused.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(["record", str(repo), "--manifest", str(m), "--criterion", "no-equals-sign"])
    check(code == 2 and "PROOF=not_recorded" in buf.getvalue(), "a criterion without '=' is refused")

    # The walkthrough: self-contained, indexed by repo#PR, outside the tree.
    m = write_manifest(proof_root, ["home.sheet", "extra.state"])
    with contextlib.redirect_stdout(io.StringIO()):
        cli.main(["record", str(repo), "--manifest", str(m), "--criterion", "home.sheet=Sheet fits the phone"])
    os.environ[walkthrough.STORE_ENV] = str(store)
    lines = walkthrough.build(repo, "demo", 42, "abc1234", "Demo title", "What changed")
    out = dict(line.split("=", 1) for line in lines)
    page = Path(out["WALKTHROUGH"]).read_text(encoding="utf-8")
    check(out["KEY"] == "demo#42" and Path(out["WALKTHROUGH"]).parent == store, f"stored by key — got {out}")
    check(page.count("data:image/png;base64,") == 8 and "Sheet fits the phone" in page
          and "abc1234" in page and "Other captured state" in page,
          "the page embeds every leg, the criterion, the merge SHA and the other state")
    check("<script" not in page and "src=\"http" not in page, "the page is self-contained")
    index = json.loads((store / "index.json").read_text(encoding="utf-8"))
    check(index["demo#42"]["sha"] == "abc1234", "index.json maps repo#PR to the walkthrough")
    check(Path(out["SEND_PATH"]).is_file() and Path(out["SEND_PATH"]).parts[-3:-1] == ("claude", "ui-proof"),
          "a sendable copy sits under %TEMP%/claude/ui-proof")
    status = run_git(["-C", str(repo), "status", "--porcelain"]).stdout.strip()
    check(status == "", f"nothing was written into the tree — got {status!r}")

    # A repo with no design-gated surface is untouched by all of this.
    plain = init_repo()
    try:
        code, out, _ = run_check(plain, base="HEAD")
        check(out["VISUAL_PROOF"] == "n/a" and out["ENFORCED"] == "no" and code == 0,
              f"SPEC_APPLIES=no is n/a — got {out}")
    finally:
        shutil.rmtree(plain, ignore_errors=True)
finally:
    os.environ.pop(walkthrough.STORE_ENV, None)
    for path in (repo, proof_root, store):
        shutil.rmtree(path, ignore_errors=True)


_h.report_and_exit("ui_proof")
