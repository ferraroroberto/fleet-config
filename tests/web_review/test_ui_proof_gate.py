"""Tests for `ui_proof gate` (skills/_lib/ui_proof/merge_gate.py), the merge hook's check.

Against real temp repos, with the two `gh` lookups replaced by fakes so no
network is touched: a repo with no UX surface passes before any `gh` call; a
UI diff on the checked-out PR branch is refused without `.proof/<PR>/index.html`
and passes with it; a backend-only diff passes; with the branch not checked
out, the PR's own file list decides (a mobile PWA-shell file included); every
fact the gate cannot establish is `GATE=unknown`, exit 3.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_ui_proof_gate.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402
from ui_proof import merge_gate  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

_h = CheckHarness()
check = _h.check

SLUG = "acme/widget"
CLAUDE_MD = "# Project\n\n## UX surface\n- design spec applies: yes\n- paths:\n  - app/static/**/*.css\n"


def git(repo: Path, *args: str) -> None:
    res = run_git(["-C", str(repo), *args])
    assert res.returncode == 0, f"git {args}: {res.stderr}"


def commit(repo: Path, rel: str, text: str, msg: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def make_repo(claude_md: str) -> Path:
    repo = init_repo(empty_commit=False)
    commit(repo, "CLAUDE.md", claude_md, "base")
    commit(repo, ".gitignore", ".proof/\n", "ignore proof")
    git(repo, "remote", "add", "origin", f"https://github.com/{SLUG}.git")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    return repo


class Fakes:
    """Stand-ins for `gh pr view` / `gh pr diff` that record whether they were asked."""

    def __init__(self, head: str = "feat/x", files=None, pr_ok: bool = True) -> None:
        self.head, self.files, self.pr_ok, self.calls = head, files, pr_ok, []

    def pr(self, selector: str, slug: str):
        self.calls.append(("pr", selector, slug))
        return {"number": 7, "headRefName": self.head, "baseRefName": "main"} if self.pr_ok else None

    def diff(self, number: int, slug: str):
        self.calls.append(("diff", number, slug))
        return self.files


def gate(repo: Path, fakes: Fakes, selector="7", slug=None):
    code, lines = merge_gate.run(repo, selector, slug, fakes.pr, fakes.diff)
    return code, dict(line.split("=", 1) for line in lines)


plain = make_repo("# Project\n")
ux = make_repo(CLAUDE_MD)
try:
    # No UX surface: pass before any gh call.
    fakes = Fakes()
    code, out = gate(plain, fakes)
    check(code == 0 and out["GATE"] == "pass" and out["VISUAL_PROOF"] == "n/a" and not fakes.calls,
          f"no UX surface passes with no gh call — got {code} {out} calls={fakes.calls}")

    # The PR branch is checked out here and touches view JS: refused without the page.
    git(ux, "checkout", "-q", "-b", "feat/x")
    commit(ux, "app/static/view.js", "// v2\n", "view")
    code, out = gate(ux, Fakes())
    check(code == 1 and out["GATE"] == "refuse" and out["PROOF_PAGE"] == "missing"
          and ".proof/7/index.html" in out["NEED"] and "feat/x checkout" in out["DETAIL"],
          f"UI diff on the checked-out branch without a page is refused — got {code} {out}")

    # The same PR with its page present passes.
    page = ux / ".proof" / "7" / "index.html"
    page.parent.mkdir(parents=True)
    page.write_text("<html></html>", encoding="utf-8")
    code, out = gate(ux, Fakes())
    check(code == 0 and out["GATE"] == "pass" and out["PROOF_PAGE"] == "present",
          f"the page present lets the merge through — got {code} {out}")
    shutil.rmtree(ux / ".proof")

    # Merging from a checkout that is not on the PR branch: the PR's file list decides.
    git(ux, "checkout", "-q", "main")
    code, out = gate(ux, Fakes(files=["app/static/view.js"]))
    check(code == 1 and out["TOUCHED"] == "yes" and "file list" in out["DETAIL"],
          f"branch not checked out: the PR's UI file is refused — got {code} {out}")
    code, out = gate(ux, Fakes(files=["app/webapp/static/manifest.webmanifest"]))
    check(code == 1 and out["TOUCHED"] == "yes",
          f"a mobile PWA-shell file is a UX diff — got {code} {out}")
    code, out = gate(ux, Fakes(files=["server.py", "README.md"]))
    check(code == 0 and out["VISUAL_PROOF"] == "n/a" and out["TOUCHED"] == "no",
          f"a backend/docs-only PR passes — got {code} {out}")

    # A backend-only diff on the checked-out branch passes too.
    git(ux, "checkout", "-q", "-b", "feat/backend")
    commit(ux, "server.py", "x = 1\n", "backend")
    code, out = gate(ux, Fakes(head="feat/backend"))
    check(code == 0 and out["TOUCHED"] == "no", f"backend-only branch passes — got {code} {out}")

    # No PR argument: the current branch names it.
    fakes = Fakes(head="feat/backend")
    gate(ux, fakes, selector=None)
    check(fakes.calls[0] == ("pr", "feat/backend", SLUG), f"no selector uses the current branch — got {fakes.calls}")

    # Unknown, never a pass.
    code, out = gate(ux, Fakes(pr_ok=False))
    check(code == 3 and out["GATE"] == "unknown" and "gh pr view" in out["REASON"],
          f"gh pr view failing is unknown — got {code} {out}")
    code, out = gate(ux, Fakes(head="feat/gone", files=None))
    check(code == 3 and "gh pr diff" in out["REASON"], f"gh pr diff failing is unknown — got {code} {out}")
    code, out = gate(ux, Fakes(), slug="acme/no-such-checkout-here")
    check(code == 3 and "no local checkout of acme/no-such-checkout-here" in out["REASON"],
          f"-R for a repo with no local checkout is unknown — got {code} {out}")
    fakes = Fakes(head="feat/backend")
    code, out = gate(ux, fakes, selector="https://github.com/acme/no-such-checkout-here/pull/9")
    check(code == 3 and "acme/no-such-checkout-here" in out["REASON"] and not fakes.calls,
          f"a PR URL names its own repo, outranking the cwd — got {code} {out}")
    code, out = gate(ux, Fakes(head="feat/backend"), selector=f"https://github.com/{SLUG}/pull/7")
    check(code == 0 and out["PR"] == f"{SLUG}#7", f"a PR URL for the cwd's repo resolves here — got {code} {out}")
    code, out = gate(ux, Fakes(head="feat/backend"), slug=SLUG.upper())
    check(code == 0 and out["PR"] == f"{SLUG}#7", f"-R naming the cwd's own repo (any case) resolves to it — got {code} {out}")
    git(ux, "remote", "set-url", "origin", "file:///nowhere")
    code, out = gate(ux, Fakes())
    check(code == 3 and "GitHub origin" in out["REASON"], f"no GitHub origin is unknown — got {code} {out}")
finally:
    for path in (plain, ux):
        shutil.rmtree(path, ignore_errors=True)


_h.report_and_exit("ui_proof_gate")
