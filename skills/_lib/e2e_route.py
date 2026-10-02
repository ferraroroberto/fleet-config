"""Deterministic front-end for the `/e2e` skill (fleet-config#556).

Single source of truth for the mechanically-checkable half of "what e2e work
does this repo/diff need?" — so the skill (and the issue-* flows that delegate
to it) measure with a helper instead of re-deriving facts by eye, the same
principle as `ux_surface.py` and `e2e_test_audit.py`.

The routing *mechanism* itself is project-scaffolding's: each repo's own
`scripts/classify_e2e.py` reads that repo's `.fleet.toml` `[e2e]` table and
maps the diff to a tier (`skip` / `static` / `full`, fail-safe to `full` —
see project-scaffolding `docs/e2e-routing.md`), plus `surface`: a narrowing of
`full` to one declared `[[e2e.surface]]`'s space-separated targets
(project-scaffolding#258). This module never re-implements
the classification; it locates, runs, boots, and reports around it.

Subcommands:

  probe <repo-root> [--scaffold <path>]
      Facts only, no git, no execution. Prints:
        CLASSIFIER=present|absent            scripts/classify_e2e.py
        CLASSIFIER_MATCHES_SCAFFOLD=yes|no|n/a
        E2E_TABLE=present|absent|invalid     [e2e] in .fleet.toml
        SUITE=present|absent                 test files under tests/e2e/
        SUITE_DIR=tests/e2e
        WEB_SURFACE=yes|no                   is this a webapp/Streamlit repo?
        WEB_KIND=webapp|streamlit|none
        WEB_REASON=<short>
        GATE_ROUTING=ok|broken|latent|not-consumed|unknown|n/a
        GATE_ROUTING_REASON=<short>
      WEB_* drives the skill's "no suite at all — worth adding one?"
      evaluation, which applies to web-surfaced repos only. GATE_ROUTING is
      the gate contract (fleet-config#1134): whether the repo's own gate
      scripts run the classifier at all, and split its space-joined
      `E2E_PYTEST_TARGET` before pytest (home-automation#784 did not).

  route <repo-root> [files...] [--scaffold <path>]
      Run the repo's own classifier (cwd = repo root, this interpreter — the
      classifier is stdlib-only and needs 3.11+ for tomllib) and pass its
      `E2E_*` lines through verbatim, prefixed with `SOURCE=classifier`.
      Classifier absent → `SOURCE=judgment` + `E2E_TIER=unknown` (the skill's
      LLM judgment layer decides, fail-safe full). Classifier errors →
      `SOURCE=classifier-error` + `E2E_TIER=full` — uncertainty always
      escalates, never narrows. So does a verdict this helper cannot vouch
      for: a tier outside `skip`/`static`/`full`/`surface`, a `surface`
      with no `E2E_SURFACE` name or an empty target list, or any `E2E_*` key
      printed twice (fleet-config#902).

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
import fleet_toml  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

DEFAULT_SCAFFOLD = Path("E:/automation/project-scaffolding")
CLASSIFIER_REL = Path("scripts/classify_e2e.py")
SUITE_REL = Path("tests/e2e")
KNOWN_TIERS = ("skip", "static", "full", "surface")
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
    data, state = fleet_toml.load_state(repo)
    if data is None:
        return state
    return "present" if fleet_toml.table(data, "e2e") is not None else "absent"


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
    layer = (fleet_toml.load(repo) or {}).get("layer")
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


def unusable_verdict(e2e_lines: List[str]) -> Optional[str]:
    """Why a classifier's `E2E_*` block can't be honoured, or None if it can.

    The helper passes a verdict through verbatim, so it must never pass one
    that narrows on nothing: an unrecognised tier, or a `surface` that names
    no surface or no targets, would otherwise run an empty slice. A key
    printed twice means the classifier contradicted itself, so neither
    value is trusted.
    """
    keys = [ln.split("=", 1)[0] for ln in e2e_lines if "=" in ln]
    repeated = sorted({k for k in keys if keys.count(k) > 1})
    if repeated:
        return f"repeated {', '.join(repeated)} lines"
    kv = dict(ln.split("=", 1) for ln in e2e_lines if "=" in ln)
    tier = kv.get("E2E_TIER", "")
    if tier not in KNOWN_TIERS:
        return f"unrecognised tier {tier!r}"
    if tier == "surface" and (not kv.get("E2E_SURFACE", "").strip()
                              or not kv.get("E2E_PYTEST_TARGET", "").split()):
        return "surface verdict without a surface name or targets"
    return None


def files_identical(a: Path, b: Path) -> bool:
    ha, hb = _sha1(a), _sha1(b)
    return ha is not None and ha == hb


# ---- gate contract: does the repo's gate honour the classifier's targets? (fleet-config#1134) ----------

TARGET_KEY = "E2E_PYTEST_TARGET"
GATE_GLOBS = ("scripts/*.ps1", "scripts/*.sh", "scripts/*.bat", "*.ps1", "*.bat", "*.sh",
              ".github/workflows/*.yml", ".github/workflows/*.yaml")
_SPLIT_RE = re.compile(r"-split\b|\.Split\(|\.split\(|\bread\s+-r?a\b|\bshlex\.split\(", re.IGNORECASE)
_ASSIGN_RES = (
    re.compile(r"^\s*\$(\w+)\s*=", re.MULTILINE),          # PowerShell: $e2eTarget = $kv["E2E_PYTEST_TARGET"]
    re.compile(r"^\s*(?:export\s+)?(\w+)=", re.MULTILINE),  # sh: TARGET=${E2E_PYTEST_TARGET}
    re.compile(r"^\s*(\w+)\s*=", re.MULTILINE),             # Python: target = kv["E2E_PYTEST_TARGET"]
)


def target_split(text: str) -> Optional[str]:
    """`yes` / `no` / `unknown` for one gate file's handling of `E2E_PYTEST_TARGET`, None when it never reads it.

    The classifier prints several targets space-joined (a declared surface,
    or two lone e2e modules a diff touched), so a gate that passes the value
    as one pytest argument exits 4 on every multi-module branch:
    home-automation#784, while app-launcher's gate split it all along. `yes`:
    the line reading the key splits it, or the variable it lands in is split
    later; `no`: it lands in a variable that is never split; `unknown`: the
    file reads the key in a shape this check cannot follow.
    """
    if TARGET_KEY not in text:
        return None
    lines = [ln for ln in text.splitlines() if TARGET_KEY in ln and not ln.lstrip().startswith("#")]
    if not lines:
        return None
    if any(_SPLIT_RE.search(ln) for ln in lines):
        return "yes"
    names = set()
    for ln in lines:
        for rx in _ASSIGN_RES:
            m = rx.match(ln)
            if m and m.group(1) != TARGET_KEY:
                names.add(m.group(1))
                break
    if not names:
        return "unknown"
    for name in names:
        n = re.escape(name)
        if re.search(rf"\${n}\s+-split\b|\${n}\.Split\(|\b{n}\.split\(|read\s+-r?a\s+\w+\s*<<<\s*\"?\$\{{?{n}\b",
                     text, re.IGNORECASE):
            return "yes"
    return "no"


def gate_contract(repo: Path) -> Tuple[str, str, List[Tuple[str, str]]]:
    """`(verdict, reason, [(file, split)])` for how the repo's gate consumes the classifier.

    `n/a` (no classifier), `not-consumed` (no gate script runs it: the
    `[e2e]` table is advice only and the gate runs whatever it hardcodes, as
    voice-transcriber's did while its routing rules were tuned), `ok`,
    `broken` (a reader never splits and the classifier can emit several
    targets), `latent` (never split, but this classifier emits one target),
    `unknown` (a reader this check cannot follow, or the classifier is run but
    no file reads the key).
    """
    classifier = repo / CLASSIFIER_REL
    if not classifier.is_file():
        return "n/a", "no scripts/classify_e2e.py", []
    readers: List[Tuple[str, str]] = []
    runs = False
    seen = set()
    for pattern in GATE_GLOBS:
        for p in sorted(repo.glob(pattern)):
            if p in seen or not p.is_file():
                continue
            seen.add(p)
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            runs = runs or "classify_e2e" in text
            split = target_split(text)
            if split is not None:
                readers.append((p.relative_to(repo).as_posix(), split))
    if not readers:
        if runs:
            return "unknown", "a gate runs classify_e2e.py but no script reads E2E_PYTEST_TARGET", []
        return "not-consumed", "no gate script runs classify_e2e.py: the [e2e] table changes nothing the gate runs", []
    bad = [f for f, s in readers if s == "no"]
    if bad:
        try:
            multi = 'Routing("surface"' in classifier.read_text(encoding="utf-8", errors="replace")
        except OSError:
            multi = True
        if multi:
            return "broken", f"{', '.join(bad)} passes the space-joined targets as one pytest argument (exit 4 on a two-module diff)", readers
        return "latent", f"{', '.join(bad)} never splits the target; this classifier emits one target today", readers
    if any(s == "unknown" for _, s in readers):
        return "unknown", "a gate reads E2E_PYTEST_TARGET in a shape this check cannot follow", readers
    return "ok", "every gate reading E2E_PYTEST_TARGET splits it", readers


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
    gate, gate_reason, _readers = gate_contract(repo)
    print(f"GATE_ROUTING={gate}")
    print(f"GATE_ROUTING_REASON={gate_reason}")
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
    unusable = unusable_verdict(e2e_lines)
    if unusable:
        print("SOURCE=classifier-error")
        print("E2E_TIER=full")
        print(f"E2E_REASON=classifier verdict unusable ({unusable}) - fail-safe full")
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
