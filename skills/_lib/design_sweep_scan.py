"""One deterministic Python sweep of the fleet's design-sync web-app population.

Why this exists
----------------
`/design-sweep` (fleet-config#180) is the fleet-wide, unattended half of
`/design-sync`: one weekly run sweeps every FastAPI + static-PWA web app and
files/refreshes its `design-drift` issue. Before dispatching a per-repo
sub-agent, the orchestrator needs to know *which* fleet repos are actually
token-styled web apps — so it skips non-web repos and Streamlit-only POC spikes
without an LLM loop reading every repo's CSS.

This script does that gate in **one process**: crawl every `ferraroroberto`
repo under a root (the same filesystem crawl `/audit-fleet`'s
`fleet_audit_scan.py` uses — skips linked worktrees whose `.git` is a file),
classify each with the *same* web-app detection `/design-sync`'s step 2
describes (token-bearing CSS in a `:root`, minus Streamlit spikes), and print
one JSON object bucketing every repo into `web_apps` / `skipped_non_web` /
`skipped_streamlit` / `errors`. The orchestrator just reads the output — no
per-repo `git`/`gh` tool calls needed for the gating step.

The correctness-critical piece (`classify_web_app`) is pure over a filesystem
tree and unit-tested (`tests/run_acceptance.py`) independent of the git/gh I/O
around it, mirroring `fleet_audit_scan.is_fleet_repo`. Reuses `design_lint`'s
`repo_files` / `parse_custom_props` so the detection can never drift from the
per-repo lint. stdlib + the `git` CLI only.

CLI
---
  design_sweep_scan.py --root <path> [--only <repo-name>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_issue  # noqa: E402
import design_lint  # noqa: E402
import fleet_audit_scan  # noqa: E402


def _token_css_files(root: Path) -> list[Path]:
    """Tracked, non-spike CSS files that define `--custom-props` in a :root/dark block.

    Reuses `design_lint.repo_files` (which already drops `spike`/`spikes` dirs
    via `SKIP_DIR_PARTS`) and `parse_custom_props`, so "is this a token-styled
    stylesheet" is decided by exactly the same code the per-repo lint uses.
    """
    out: list[Path] = []
    for f in design_lint.repo_files(root, (".css",)):
        themes = design_lint.parse_custom_props(design_lint.read_text(f), f.name)
        if themes.get("light") or themes.get("dark"):
            out.append(f)
    return out


def _has_fastapi_signal(root: Path) -> bool:
    """True if any tracked Python file imports FastAPI / runs uvicorn.

    Distinguishes a real FastAPI web app from a Streamlit-only POC that merely
    happens to ship a token-styled stylesheet.
    """
    for p in design_lint.repo_files(root, (".py",)):
        text = design_lint.read_text(p).lower()
        if "fastapi" in text or "uvicorn" in text:
            return True
    return False


def _is_streamlit_only(root: Path) -> bool:
    """True if the repo's primary app is Streamlit (not a FastAPI PWA).

    A tracked `streamlit_app.py` outside a spike dir marks a Streamlit app;
    a co-present FastAPI signal means it is a real web app that also has a
    Streamlit spike, so it is NOT Streamlit-only.
    """
    has_streamlit = any(p.name == "streamlit_app.py" for p in design_lint.repo_files(root, (".py",)))
    return has_streamlit and not _has_fastapi_signal(root)


def classify_web_app(root: Path) -> tuple[str, str]:
    """Classify a repo for the design sweep. Pure over the filesystem tree.

    Returns ``(category, reason)`` where category is one of:
      - ``"web"``         — a token-styled FastAPI/static-PWA web app (sweep it)
      - ``"non_web"``     — no token-bearing, non-spike stylesheet
      - ``"streamlit"``   — a Streamlit-only POC spike (out of scope)
    """
    token_files = _token_css_files(root)
    if not token_files:
        return "non_web", "no token-styled CSS (:root custom properties)"
    if _is_streamlit_only(root):
        return "streamlit", "Streamlit-only POC (streamlit_app.py, no FastAPI signal)"
    return "web", f"{len(token_files)} token-styled stylesheet(s)"


def scan(root: str, only: str | None = None) -> dict:
    results: dict = {
        "web_apps": [], "skipped_non_web": [], "skipped_streamlit": [], "errors": [],
    }

    for d in sorted(Path(root).iterdir()):
        if not d.is_dir():
            continue
        if not (d / ".git").is_dir():
            # Not a git repo, or a linked worktree (its .git is a file) — skip.
            continue
        name = d.name
        if only and name != only:
            continue

        try:
            remote = audit_issue._git(["-C", str(d), "remote", "get-url", "origin"])
        except SystemExit:
            continue
        if not fleet_audit_scan.is_fleet_repo(remote):
            continue

        try:
            category, reason = classify_web_app(d)
        except OSError as exc:
            results["errors"].append({"repo": name, "reason": str(exc)})
            continue

        if category == "web":
            results["web_apps"].append({"repo": name, "path": str(d)})
        elif category == "streamlit":
            results["skipped_streamlit"].append({"repo": name, "reason": reason})
        else:
            results["skipped_non_web"].append({"repo": name, "reason": reason})

    return results


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="One-pass fleet-wide design-sync web-app gate.")
    ap.add_argument("--root", required=True)
    ap.add_argument("--only", default=None)
    args = ap.parse_args(argv)

    print(json.dumps(scan(args.root, args.only)))


if __name__ == "__main__":
    main()
