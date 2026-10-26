"""Browser leg of the design-review suite (fleet-config#971).

Drives `measure` and the walk against the static fixture pages in
`tests/fixtures/design_review/` through whichever sibling fleet checkout has Playwright in its
`.venv` (project-scaffolding, then app-launcher). About 78 walks, each two real browser launches
(light + dark): ~34 of the gate's ~40 minutes. The gate runs it only when the design-review
surface changed (`tests/acceptance/gate_routing.py`); `GATE_FULL=1` forces it. No sibling
Playwright is reported as a skip — never a pass.

The pure-logic leg is the suites in `tests/design_review/`.

The walks are ordered modules in this folder, imported one after another: they share the sibling interpreter,
the throwaway state dir and a few results (`by_id`), so each part imports what an earlier one built (see `_suite.py`).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_review_browser/test_design_review_browser.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

from _suite import _h, interp
from acceptance.shared import SKIP_EXIT

if interp is None:
    _h.skip("browser leg: no sibling .venv with Playwright (project-scaffolding, app-launcher) -- fixture walk NOT verified")
else:
    import measure_fixture  # noqa: F401
    import layout_rules  # noqa: F401
    import text_rules  # noqa: F401
    import type_and_overlay_rules  # noqa: F401
    import popovers_and_walks  # noqa: F401
    import inventory_and_e2e  # noqa: F401

_h.report_and_exit("test_design_review_browser", skip_code=SKIP_EXIT)
