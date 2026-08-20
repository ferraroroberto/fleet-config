"""Unit tests for the pure logic in skills/_lib/deploy_coverage.py (fleet-config#459).

No live git — these exercise the declared-component parser (fence-skipping,
the four-bullet template), the path-token filter, the diff-touch matcher, and
the three-state (`yes`/`no`/`unknown`) touch decision that feeds
/issue-finish's "merged but not yet live" wording.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_deploy_coverage.py`
(also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import deploy_coverage as dc  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- template parsing ----

TEMPLATE_SHAPED = """\
# Project Instructions

## This repository
Some prose about the repo.

## session_host
- what/why: the `:8446` session-host owns every live PTY; excluded from `tray.bat --restart` to protect them. Covers `src/session_host.py` and `app/session_host/`.
- update command: `pwsh -File scripts/restart-session-host.ps1 -Confirm`
- liveness signal: `GET /api/version`'s `session_host.stale`
- NOT restarted/deployed by: `tray.bat --restart`

## Internal architecture
More prose — must not leak into the session_host block above.
"""

comps = dc.parse_components(TEMPLATE_SHAPED)
check(len(comps) == 1, "one declared component found")
comp = comps[0]
check(comp["name"] == "session_host", "component name is the heading text")
check(comp["liveness_signal"] == "`GET /api/version`'s `session_host.stale`", "liveness signal captured verbatim")
check(comp["update_command"] == "`pwsh -File scripts/restart-session-host.ps1 -Confirm`", "update command captured verbatim")
check(comp["paths"] == ["src/session_host.py", "app/session_host/"], "path-looking backtick tokens extracted from what/why")

# a section with no `liveness signal:` bullet is not a declared component —
# an ordinary heading (e.g. "## Internal architecture") must never register.
check(dc.parse_components("## Internal architecture\n- some other bullet: x\n") == [],
      "a section without a liveness-signal bullet is not a component")

# no CLAUDE.md content / no components at all -> empty list, not an error.
check(dc.parse_components("") == [], "empty text yields no components")
check(dc.parse_components("# Title\n\nJust prose, no headings.\n") == [], "prose-only text yields no components")


# ---- fenced example must not be read as a live declaration ----
# project-scaffolding#199/#200 documents this exact template inside a fenced
# code block as a copy-paste default. A repo whose CLAUDE.md merely shows the
# template (as the scaffold's own does) must not be treated as having
# declared a live component named "<component name>".
FENCED = """\
# Some CLAUDE.md that only *documents* the template

Declare every not-fully-covered component like this:

```markdown
## <component name>
- what/why: <what this component is; why it's excluded>
- update command: `<the one supported command>`
- liveness signal: `<field or probe>` — e.g. `GET /api/version`'s `<component>.stale`
- NOT restarted/deployed by: `<the standard restart/finish flow>`
```

That's the whole file — no live block.
"""
check(dc.parse_components(FENCED) == [], "fenced template heading is ignored, not read as a live component")

# a real component declared *alongside* an unrelated fenced example in the
# same file — the fence must not swallow the real section that follows it.
MIXED = FENCED.rstrip() + "\n\n" + TEMPLATE_SHAPED
mixed_comps = dc.parse_components(MIXED)
check(len(mixed_comps) == 1 and mixed_comps[0]["name"] == "session_host",
      "a real declaration after a fenced example is still found")


# ---- app-launcher's real CLAUDE.md, as it stood on 2026-07-27 (fleet-config#459) ----
# app-launcher#615 is the proven reference implementation the scaffold's
# convention generalizes from, but app-launcher's own CLAUDE.md predates the
# template and states the session-host staleness rule as ordinary prose
# bullets under "## This repository", not as its own `## session_host`
# heading with the four labeled bullets. This is a frozen excerpt (not a live
# fetch — tests must not depend on the network), used to prove the parser
# neither crashes nor false-positives on real, complex, someone-else-written
# text it was not designed around. Until app-launcher migrates to the
# template shape, DECLARED is correctly "no" for it — a true negative, not a
# parser gap.
APP_LAUNCHER_EXCERPT = """\
# Project Instructions

## This repository
Phone-first launcher hub for the rest of the home stack.

**Project specifics:**

- **Restart and verify before hand-off:** the running webapp has no hot-reload. The canonical restart is **`tray.bat --restart`**. **The `:8446` session-host is detach-compliant (project-scaffolding#35):** it is excluded from the reclaim sweep, and the fresh tray re-adopts it on start.
- **A session-host change is not live until `:8446` itself restarts — `tray.bat --restart` will never do that for you (#615).** A diff touching `src/session_host.py` or `app/session_host/` can merge, pass a fully green gate, and go through a correct `tray.bat --restart` while the running session-host keeps executing whatever it loaded. Check `GET /api/version`'s `session_host` block: `{"reachable", "git_sha", "started_at", "stale"}`. `stale: true` means **report the change as merged but not yet live**. The one supported way to restart it is `pwsh -File scripts/restart-session-host.ps1 -Confirm`.

## Internal architecture
More prose.
"""
check(dc.parse_components(APP_LAUNCHER_EXCERPT) == [],
      "app-launcher's real (pre-template) CLAUDE.md declares nothing by this parser's structured rule — true negative, no crash")


# ---- path-token filter ----

check(dc._looks_like_path("src/session_host.py"), "file path with extension + slash")
check(dc._looks_like_path("app/session_host/"), "directory path (trailing slash)")
check(dc._looks_like_path("GET /api/version"), "verb-prefixed endpoint path is unwrapped")
check(not dc._looks_like_path("pwsh -File scripts/restart-session-host.ps1 -Confirm"),
      "a multi-word shell command (not a verb+path pair) is not a path")
check(not dc._looks_like_path("session_host.stale"), "a dotted field name with no slash is not a path")
check(not dc._looks_like_path(""), "empty token is not a path")


# ---- touched_by: prefix match for dirs, exact/suffix match for files ----

PATHS = ["src/session_host.py", "app/session_host/"]
check(dc.touched_by(["src/session_host.py"], PATHS) == ["src/session_host.py"], "exact file match")
check(dc.touched_by(["app/session_host/server.py"], PATHS) == ["app/session_host/server.py"],
      "file under a declared directory matches by prefix")
check(dc.touched_by(["README.md", "app/webapp/static/x.css"], PATHS) == [],
      "unrelated files do not match")
check(dc.touched_by(["src\\session_host.py"], PATHS) == ["src/session_host.py"],
      "backslash paths are normalized before matching")


# ---- component_touch_status: the three-state decision ----

touched_comp = {"paths": ["src/session_host.py"]}
check(dc.component_touch_status(touched_comp, ["src/session_host.py", "README.md"]) == "yes",
      "touched -> yes")
check(dc.component_touch_status(touched_comp, ["README.md"]) == "no",
      "not touched -> no")
no_paths_comp = {"paths": []}
check(dc.component_touch_status(no_paths_comp, ["README.md"]) == "unknown",
      "no parseable paths -> unknown, never silently 'no' (the exact #199 failure shape)")


# ---- cmd_check: a diff that FAILED is `unknown` too (fleet-config#681) ----
# The three-state decision above only ever saw "no parseable path token". The
# other way this flow cannot tell whether it was touched is the diff itself
# failing — which the old private `_changed_files` turned into `[]`, and `[]`
# against a component WITH paths prints `TOUCHED=no`: the deploy-coverage gate
# declaring a component untouched on the strength of a probe that never ran.
import contextlib  # noqa: E402
import io  # noqa: E402
import tempfile  # noqa: E402


def _run_dc_check(changed_stub):
    repo = Path(tempfile.mkdtemp(prefix="test_deploy_coverage_"))
    (repo / "CLAUDE.md").write_text(TEMPLATE_SHAPED, encoding="utf-8")
    orig_changed, orig_base = dc.git_run.changed_files, dc._default_base
    dc.git_run.changed_files = lambda *a, **k: changed_stub
    dc._default_base = lambda *_a, **_k: "origin/main"
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            dc.cmd_check(repo, None)
        return buf.getvalue()
    finally:
        dc.git_run.changed_files, dc._default_base = orig_changed, orig_base
        import shutil
        shutil.rmtree(repo, ignore_errors=True)


_dc_out = _run_dc_check(None)
check("TOUCHED=unknown" in _dc_out,
      f"cmd_check: a failed diff reports TOUCHED=unknown for a declared component — got {_dc_out.splitlines()!r}")
check("LIVENESS=" in _dc_out and "UPDATE_CMD=" in _dc_out,
      "cmd_check: the unknown stanza still carries LIVENESS/UPDATE_CMD so /issue-finish can act on it")
check("TOUCHED=no" in _run_dc_check(["README.md"]),
      "cmd_check: an untouched component on a real diff still reports TOUCHED=no")
check("TOUCHED=yes" in _run_dc_check(["src/session_host.py"]),
      "cmd_check: a touched component still reports TOUCHED=yes")


_h.report_and_exit("deploy_coverage")
