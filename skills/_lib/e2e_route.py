"""Deterministic front-end for the `/e2e` skill (fleet-config#556).

Single source of truth for the mechanically-checkable half of "what e2e work
does this repo/diff need?" — so the skill (and the issue-* flows that delegate
to it) measure with a helper instead of re-deriving facts by eye, the same
principle as `ux_surface.py` and `e2e_test_audit.py`.

The routing *mechanism* itself is project-scaffolding's: each repo's own
`scripts/classify_e2e.py` reads that repo's `.fleet.toml` `[e2e]` table and
maps the diff to a tier (`skip` / `static` / `full`, fail-safe to `full` —
see project-scaffolding `docs/e2e-routing.md`). This module never re-implements
the classification; it locates, runs, boots, and reports around it.

Subcommands:

  probe <repo-root> [--scaffold <path>]
      Facts only, no git, no execution. Prints:
        CLASSIFIER=present|absent            scripts/classify_e2e.py
        CLASSIFIER_MATCHES_SCAFFOLD=yes|no|n/a
        E2E_TABLE=present|absent|invalid     [e2e] in .fleet.toml (tomllib)
        SUITE=present|absent                 test files under tests/e2e/
        SUITE_DIR=tests/e2e
        WEB_SURFACE=yes|no                   is this a webapp/Streamlit repo?
        WEB_KIND=webapp|streamlit|none
        WEB_REASON=<short>
      WEB_* drives the skill's "no suite at all — worth adding one?"
      evaluation, which applies to web-surfaced repos only.

  route <repo-root> [files...] [--scaffold <path>]
      Run the repo's own classifier (cwd = repo root, this interpreter — the
      classifier is stdlib-only and needs 3.11+ for tomllib) and pass its
      `E2E_*` lines through verbatim, prefixed with `SOURCE=classifier`.
      Classifier absent → `SOURCE=judgment` + `E2E_TIER=unknown` (the skill's
      LLM judgment layer decides, fail-safe full). Classifier errors →
      `SOURCE=classifier-error` + `E2E_TIER=full` — uncertainty always
      escalates, never narrows.

  bootstrap <repo-root> [--scaffold <path>] [--force]
      Self-healing adoption: copy the scaffold's parameterized
      `scripts/classify_e2e.py` into the repo **byte-verbatim** and verify the
      copy. Refuses (exit 1) to overwrite an existing *different* classifier
      without `--force` — a repo with a custom classifier (e.g. app-launcher's
      pre-parameterization one) migrates deliberately, not as a side effect.
      Never writes `.fleet.toml` — the starter `[e2e]` table is per-repo
      judgment and belongs to the skill layer.

stdlib only (matches the _lib module contract).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import NO_WINDOW  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

DEFAULT_SCAFFOLD = Path("E:/automation/project-scaffolding")
CLASSIFIER_REL = Path("scripts/classify_e2e.py")
SUITE_REL = Path("tests/e2e")
_WEB_DEP = re.compile(r"\b(fastapi|flask|uvicorn|starlette)\b", re.IGNORECASE)
_STREAMLIT_DEP = re.compile(r"\bstreamlit\b", re.IGNORECASE)


# ---- pure helpers (unit-tested without git or subprocess) -----------------

def _sha1(path: Path) -> Optional[str]:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return None


def classifier_state(repo: Path, scaffold: Path) -> Tuple[str, str]:
    """`(CLASSIFIER, CLASSIFIER_MATCHES_SCAFFOLD)` for the probe output."""
    own = _sha1(repo / CLASSIFIER_REL)
    if own is None:
        return "absent", "n/a"
    ref = _sha1(scaffold / CLASSIFIER_REL)
    if ref is None:
        return "present", "n/a"
    return "present", "yes" if own == ref else "no"


def e2e_table_state(repo: Path) -> str:
    """`present` / `absent` / `invalid` for the `.fleet.toml` `[e2e]` table."""
    fleet_toml = repo / ".fleet.toml"
    if not fleet_toml.is_file():
        return "absent"
    import tomllib
    try:
        data = tomllib.loads(fleet_toml.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return "invalid"
    return "present" if isinstance(data.get("e2e"), dict) else "absent"


def suite_state(repo: Path) -> str:
    """`present` when tests/e2e/ holds at least one test module."""
    suite = repo / SUITE_REL
    if not suite.is_dir():
        return "absent"
    return "present" if any(suite.rglob("test_*.py")) else "absent"


def _dependency_text(repo: Path) -> str:
    """Concatenated dependency-declaration text (requirements* + pyproject)."""
    chunks: List[str] = []
    for req in sorted(repo.glob("requirements*.txt")):
        try:
            chunks.append(req.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    pyproject = repo / "pyproject.toml"
    if pyproject.is_file():
        try:
            chunks.append(pyproject.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n".join(chunks)


def _fleet_layer(repo: Path) -> Optional[str]:
    fleet_toml = repo / ".fleet.toml"
    if not fleet_toml.is_file():
        return None
    import tomllib
    try:
        data = tomllib.loads(fleet_toml.read_text(encoding="utf-8", errors="replace"))
    except tomllib.TOMLDecodeError:
        return None
    layer = data.get("layer")
    return layer if isinstance(layer, str) else None


def detect_web_surface(repo: Path) -> Tuple[str, str, str]:
    """`(WEB_SURFACE, WEB_KIND, WEB_REASON)`.

    Declared signal first — `.fleet.toml` `layer = "working-web"` is the
    fleet's own statement that this repo serves a web UI. Heuristics second:
    a Streamlit dependency/entrypoint, then a web-framework dependency. A
    pipeline repo with neither reads `no`, which is what keeps the skill's
    "worth adding a suite?" evaluation off non-web repos by construction.
    """
    if _fleet_layer(repo) == "working-web":
        return "yes", "webapp", ".fleet.toml layer=working-web"
    deps = _dependency_text(repo)
    if _STREAMLIT_DEP.search(deps) or (repo / "streamlit_app.py").is_file():
        return "yes", "streamlit", "streamlit dependency/entrypoint"
    m = _WEB_DEP.search(deps)
    if m:
        return "yes", "webapp", f"{m.group(1).lower()} dependency"
    return "no", "none", "no web framework signal"


def files_identical(a: Path, b: Path) -> bool:
    ha, hb = _sha1(a), _sha1(b)
    return ha is not None and ha == hb


# ---- subcommands ----------------------------------------------------------

def cmd_probe(repo: Path, scaffold: Path) -> int:
    classifier, matches = classifier_state(repo, scaffold)
    web, kind, reason = detect_web_surface(repo)
    print(f"CLASSIFIER={classifier}")
    print(f"CLASSIFIER_MATCHES_SCAFFOLD={matches}")
    print(f"E2E_TABLE={e2e_table_state(repo)}")
    print(f"SUITE={suite_state(repo)}")
    print(f"SUITE_DIR={SUITE_REL.as_posix()}")
    print(f"WEB_SURFACE={web}")
    print(f"WEB_KIND={kind}")
    print(f"WEB_REASON={reason}")
    return 0


def cmd_route(repo: Path, files: List[str]) -> int:
    classifier = repo / CLASSIFIER_REL
    if not classifier.is_file():
        print("SOURCE=judgment")
        print("E2E_TIER=unknown")
        print("E2E_REASON=no classifier - LLM judgment layer decides (fail-safe: full)")
        return 0
    try:
        res = subprocess.run(
            [sys.executable, str(classifier), *files],
            cwd=str(repo), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120, creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("SOURCE=classifier-error")
        print("E2E_TIER=full")
        print(f"E2E_REASON=classifier failed to run ({type(exc).__name__}) - fail-safe full")
        return 0
    e2e_lines = [ln for ln in res.stdout.splitlines() if ln.startswith("E2E_")]
    if res.returncode != 0 or not any(ln.startswith("E2E_TIER=") for ln in e2e_lines):
        print("SOURCE=classifier-error")
        print("E2E_TIER=full")
        print(f"E2E_REASON=classifier exit {res.returncode} without a tier - fail-safe full")
        return 0
    print("SOURCE=classifier")
    for ln in e2e_lines:
        print(ln)
    return 0


def cmd_bootstrap(repo: Path, scaffold: Path, force: bool) -> int:
    src = scaffold / CLASSIFIER_REL
    if not src.is_file():
        print(f"BOOTSTRAP=error REASON=scaffold classifier not found at {src}")
        return 1
    dest = repo / CLASSIFIER_REL
    if dest.is_file():
        if files_identical(src, dest):
            print("BOOTSTRAP=exists-identical")
            print(f"DEST={dest}")
            return 0
        if not force:
            print("BOOTSTRAP=refused REASON=existing classifier differs from scaffold "
                  "(custom implementation - migrate deliberately with --force)")
            return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    if not files_identical(src, dest):
        print("BOOTSTRAP=error REASON=post-copy verification failed (bytes differ)")
        return 1
    print("BOOTSTRAP=copied")
    print(f"DEST={dest}")
    print(f"SHA={_sha1(dest)}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic front-end for the /e2e skill.")
    ap.add_argument("--scaffold", type=Path, default=DEFAULT_SCAFFOLD,
                    help="project-scaffolding checkout (classifier source of truth)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="repo facts: classifier/table/suite/web-surface")
    p_probe.add_argument("repo", type=Path)

    p_route = sub.add_parser("route", help="run the repo's classifier on the live diff")
    p_route.add_argument("repo", type=Path)
    p_route.add_argument("files", nargs="*", help="explicit file list (default: live diff)")

    p_boot = sub.add_parser("bootstrap", help="copy the scaffold classifier in, byte-verbatim")
    p_boot.add_argument("repo", type=Path)
    p_boot.add_argument("--force", action="store_true",
                        help="overwrite an existing, different classifier")

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    if args.cmd == "probe":
        return cmd_probe(repo, args.scaffold)
    if args.cmd == "route":
        return cmd_route(repo, args.files)
    return cmd_bootstrap(repo, args.scaffold, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
