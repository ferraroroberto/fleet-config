"""Visual-verification backend selection + Playwright fallback plan (fleet-config#351).

Single source of truth for *how the issue-* visual gate gets a real browser
render when Codex's in-app `iab` backend isn't live* — so `/issue-finish` and
`/issue-yolo` drive the same deterministic decision instead of improvising a
Playwright launch per session.

Background: `docs/codex-browser.md`. Codex ships an in-app Browser plugin
(`iab`); its *instructions* load even when its *runtime backend* isn't
registered, so `agent.browsers.list()` can return `[]`. The old recovery path
told the agent to "fall back to non-browser verification" — which cannot satisfy
the UX gate's visual leg. This module makes the fallback operational: preflight
`iab`, and when it's absent drive installed Playwright with real Chrome against
the live feature-branch app, honoring the fleet browser-safety contract.

What this module owns (the reusable, deterministic parts):
  * backend selection — prefer `iab`, fall back to Playwright;
  * the browser-safety launch kwargs (real Chrome + stealth markers + persistent
    profile + 1280x900) as data, so the fallback composes the global rule rather
    than re-inlining a launch — see global-CLAUDE.md's "Browser automation must
    not look like a bot";
  * the capture plan — every `KEY_VIEWS` entry x {light, dark};
  * distinct, actionable messages for the four capability failures the issue
    calls out (Playwright missing, Chrome missing, live app unreachable, profile
    lock exhausted) plus the informational `iab`-unavailable and a render failure
    — looked up per code via `failure_message()`, never dumped as a legend;
  * a `plan` CLI the skills invoke to print the backend, capture plan and launch
    contract, plus the message for any failure the plan itself determined.

What it does NOT own: launching the browser itself (the agent drives it via its
Browser plugin, or via Playwright with these kwargs) and the proprietary `iab`
runtime — that's Codex-client state, never a repo dependency (docs/codex-browser.md).

`KEY_VIEWS` come from the same `## UX surface` block `ux_surface` reads, so the
two helpers never disagree about which views the gate covers.

stdlib only (matches the _lib module contract).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ux_surface  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()


# ---- backend selection ----------------------------------------------------

BACKEND_IAB = "iab"
BACKEND_PLAYWRIGHT = "playwright"


def choose_backend(iab_available: bool) -> str:
    """Prefer the in-app `iab` backend; fall back to Playwright when it's absent.

    `iab_available` is the truthiness of `agent.browsers.list()` (i.e. did it
    return a non-empty list that includes `iab`). When live, the existing in-app
    path is used unchanged; otherwise the Playwright fallback plan applies.
    """
    return BACKEND_IAB if iab_available else BACKEND_PLAYWRIGHT


# ---- browser-safety launch contract (data, not a launch) ------------------
#
# Mirrors the four markers hooks/browser_stealth_lint.py enforces and the full
# "Browser automation must not look like a bot" rule in global-CLAUDE.md. The
# fallback composes these rather than re-inlining launch args per session.

VIEWPORT = {"width": 1280, "height": 900}

# add_init_script this so navigator.webdriver reads `undefined` (a CLI flag alone
# is not enough — it must be defined away on the page).
WEBDRIVER_INIT_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)

# Stripped from Chrome's default args so no automation infobar / IdleDetection.
IGNORE_DEFAULT_ARGS = ["--enable-automation", "--enable-blink-features=IdleDetection"]

CHROME_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=Translate",
    "--no-default-browser-check",
    "--no-first-run",
]


def playwright_launch_kwargs(profile_dir: str) -> Dict[str, object]:
    """`launch_persistent_context` kwargs implementing the browser-safety rule.

    Real Chrome (`channel="chrome"`, not bundled Chromium), automation markers
    stripped, a persistent profile at `profile_dir`, 1280x900. The caller must
    also `add_init_script(WEBDRIVER_INIT_SCRIPT)` and serialize access to the
    profile (never kill a live holder — see the shared-profile rule).
    """
    return {
        "user_data_dir": str(profile_dir),
        "channel": "chrome",
        "headless": False,
        "viewport": dict(VIEWPORT),
        "ignore_default_args": list(IGNORE_DEFAULT_ARGS),
        "args": list(CHROME_ARGS),
    }


# ---- capture plan (every KEY_VIEWS entry x light/dark) --------------------

THEMES = ("light", "dark")


def _slug(view: str) -> str:
    """A filesystem-safe stem for a view path (`/settings` -> `settings`)."""
    s = re.sub(r"[^A-Za-z0-9]+", "-", view.strip("/")).strip("-")
    return s or "root"


def capture_plan(
    key_views: List[str], base_url: str, scratch_dir: str
) -> List[Dict[str, str]]:
    """One capture spec per `KEY_VIEWS` entry x {light, dark}.

    Each spec is `{view, theme, url, screenshot}` — the screenshot path lands in
    `scratch_dir` (a local, gitignored scratch location; never committed, never
    attached to a PR/issue). Guarantees the fallback visits *every* requested
    view in both themes, in order.
    """
    base = base_url.rstrip("/")
    plan: List[Dict[str, str]] = []
    for view in key_views:
        path = view if view.startswith("/") else "/" + view
        url = base + path
        for theme in THEMES:
            plan.append(
                {
                    "view": view,
                    "theme": theme,
                    "url": url,
                    "screenshot": str(Path(scratch_dir) / f"{_slug(view)}-{theme}.png"),
                }
            )
    return plan


# ---- distinct, actionable capability failures -----------------------------
#
# The issue's contract: `iab` unavailable, Playwright/Chrome unavailable, live
# app unreachable, and a rendered-assertion/screenshot failure must NOT collapse
# into one generic message. Each code carries its own remediation, looked up one
# at a time by whoever determined the code. The plan CLI emits the message for
# the failure it determined itself and nothing else — never the whole legend for
# the caller to classify by hand; docs/codex-browser.md carries the codes only
# the driving agent can observe.

FAILURES: Dict[str, str] = {
    "IAB_UNAVAILABLE": (
        "Codex in-app Browser backend `iab` is not registered "
        "(agent.browsers.list() returned []). Not fatal — falling back to "
        "Playwright with real Chrome. To restore the in-app path, see "
        "docs/codex-browser.md (restart Codex; confirm the Browser pane)."
    ),
    "PLAYWRIGHT_MISSING": (
        "Playwright is not importable from the project's .venv "
        "(`import playwright` failed). Install it into the existing .venv "
        "(never a bare `venv`) before the visual gate can run — this is a "
        "missing-dependency error, distinct from Chrome being absent."
    ),
    "CHROME_MISSING": (
        "Real Chrome (channel=\"chrome\") is not installed for Playwright. The "
        "stealth contract requires real Chrome, not bundled Chromium — install "
        "Chrome; do NOT silently fall back to Chromium (it fails bot detection)."
    ),
    "APP_UNREACHABLE": (
        "The live feature-branch app did not respond at <base_url>. Launch it "
        "(the project's run/verify skill) and confirm the port is listening "
        "before retrying — distinct from a browser/dependency problem; the "
        "browser stack is fine, the target is down."
    ),
    "PROFILE_LOCK_EXHAUSTED": (
        "The persistent Chrome profile stayed locked past the 60/120/240/480s "
        "backoff — a sibling browser job is holding it. Do NOT kill the holder; "
        "wait and retry on the backoff schedule, or launch with a distinct "
        "profile dir. See the shared-Chrome-profile rule in global-CLAUDE.md."
    ),
    "RENDER_FAILED": (
        "Playwright launched and the app is reachable, but a capture failed "
        "(navigation, screenshot, or a visual assertion errored). Distinct from "
        "a missing browser or a down app — inspect the page error for the "
        "specific view; the render did not complete."
    ),
}


def failure_message(code: str, base_url: Optional[str] = None) -> str:
    """The distinct, actionable message for a capability-failure `code`.

    `base_url` fills the `APP_UNREACHABLE` placeholder; other codes ignore it.
    Raises `KeyError` on an unknown code (a typo shouldn't silently pass).
    """
    msg = FAILURES[code]
    if base_url:
        msg = msg.replace("<base_url>", base_url)
    return msg


# ---- environment discovery / probes ---------------------------------------


def discover_venv_python(repo_root: str) -> Optional[Path]:
    """The project's `.venv` interpreter (Windows `Scripts`, POSIX `bin`), or None.

    Honors the fleet venv rule — the *existing* `.venv`, never a bare `venv`.
    """
    root = Path(repo_root)
    for rel in ("Scripts/python.exe", "bin/python"):
        cand = root / ".venv" / rel
        if cand.is_file():
            return cand
    return None


def playwright_probe_cmd(venv_python: str) -> List[str]:
    """argv that prints Playwright's version if importable, else exits non-zero.

    Deterministic command construction (unit-tested); the caller runs it and maps
    a non-zero exit to the `PLAYWRIGHT_MISSING` failure.
    """
    return [
        str(venv_python),
        "-c",
        "import playwright; print(getattr(playwright, '__version__', 'unknown'))",
    ]


def app_reachable(base_url: str, timeout: float = 5.0) -> bool:
    """True if *something* answers HTTP at `base_url` (any status = server up).

    An `HTTPError` still means the server responded (up); only a transport error
    (`URLError`/`OSError`) or a malformed URL counts as unreachable. Used to tell
    `APP_UNREACHABLE` apart from a render failure.
    """
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url), timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def scratch_dir_for(repo_name: str) -> str:
    """A local, gitignored scratch dir for this repo's visual captures.

    System temp (skills write transient artifacts there) — never inside the repo,
    so a stray screenshot can't be committed or attached to a PR.
    """
    return str(Path(tempfile.gettempdir()) / f"fleet-visual-{repo_name}")


# ---- CLI ------------------------------------------------------------------


def _key_views(repo: Path) -> Optional[Dict[str, object]]:
    claude_md = repo / "CLAUDE.md"
    if not claude_md.is_file():
        return None
    return ux_surface.parse_ux_surface_block(
        claude_md.read_text(encoding="utf-8", errors="replace")
    )


def cmd_plan(repo: Path, base_url: str, iab_available: bool, check_app: bool) -> int:
    """Print the deterministic visual-verification plan for the skills to drive.

    KEY=VALUE lines (structured values as JSON), matching the ux_surface CLI
    style. When `iab` is available the in-app path is used and no fallback plan
    is emitted; otherwise the Playwright fallback plan (venv, probe, launch
    kwargs, captures, scratch dir) is printed, plus the remediation message for
    any failure this command determined itself (`--check-app` finding it down).
    """
    block = _key_views(repo)
    applies = bool(block and block["spec_applies"])
    backend = choose_backend(iab_available)
    print(f"BACKEND={backend}")
    print(f"SPEC_APPLIES={'yes' if applies else 'no'}")
    if not applies:
        print("KEY_VIEWS=")
        print("NOTE=no UX surface declared — nothing to render")
        return 0

    key_views = list(block["key_views"])  # type: ignore[index]
    print(f"KEY_VIEWS={','.join(key_views)}")
    if backend == BACKEND_IAB:
        print("NOTE=in-app `iab` backend live — use it (see global-CLAUDE.md browser rule)")
        return 0

    if check_app:  # opt-in live probe so the default plan stays deterministic/offline
        reachable = app_reachable(base_url)
        print(f"APP_REACHABLE={'yes' if reachable else 'no'}")
        if not reachable:
            print(f"APP_UNREACHABLE_HINT={failure_message('APP_UNREACHABLE', base_url)}")

    scratch = scratch_dir_for(repo.name)
    venv = discover_venv_python(str(repo))
    print(f"VENV_PYTHON={venv if venv else '(none found — see PLAYWRIGHT_MISSING)'}")
    if venv:
        print(f"PLAYWRIGHT_PROBE={json.dumps(playwright_probe_cmd(str(venv)))}")
    print(f"SCRATCH_DIR={scratch}")
    print(f"LAUNCH_KWARGS={json.dumps(playwright_launch_kwargs(str(Path(scratch) / 'profile')))}")
    print(f"INIT_SCRIPT={WEBDRIVER_INIT_SCRIPT}")
    print(f"CAPTURES={json.dumps(capture_plan(key_views, base_url, scratch))}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Visual-verification backend selection + Playwright fallback plan."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="print the visual-verification plan for a repo")
    p_plan.add_argument("repo", type=Path)
    p_plan.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="the live feature-branch app root (default: http://127.0.0.1:8000)",
    )
    p_plan.add_argument(
        "--iab-available",
        choices=("yes", "no"),
        default="no",
        help="did agent.browsers.list() include `iab`? (default: no -> Playwright fallback)",
    )
    p_plan.add_argument(
        "--check-app",
        action="store_true",
        help="also probe --base-url (live HTTP) and print APP_REACHABLE — off by "
        "default so the plan stays deterministic/offline",
    )

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    return cmd_plan(repo, args.base_url, args.iab_available == "yes", args.check_app)


if __name__ == "__main__":
    raise SystemExit(main())
