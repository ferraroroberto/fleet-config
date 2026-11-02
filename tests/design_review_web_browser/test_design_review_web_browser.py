"""Browser leg of the web-page design review: `measure_web` end to end against a fixture static site.

The fixture repo `tests/fixtures/design_review_web/site_repo/` has its own preview script
(`scripts/serve.py`), one page that meets every section 3 rule and three known-bad pages, one per
failure design.web.md section 3 names: `/squeeze` (the 152 px text column at 800 px, from a fixed
max track), `/menu` (a `backdrop-filter` header that collapses the fixed phone-menu panel), and
`/carousel` (fixed card widths leaving a partial card at 1440 px). Each bad page must fail its rule
and the good page must pass it, and the preview the review started must be gone afterwards.

Playwright comes from whichever sibling fleet checkout has it in its `.venv` (project-scaffolding,
then app-launcher); none is reported as a skip, never a pass. About a minute.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_review_web_browser/test_design_review_web_browser.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / "tests"))

from acceptance.shared import SKIP_EXIT  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

os.environ["CLAUDE_HOOKS_STATE_DIR"] = str(throwaway_dir("design-review-web-state-"))

from design_review import capture, plan, rubric as rubric_mod  # noqa: E402
from design_review.web import measure_web  # noqa: E402

_h = CheckHarness()
check = _h.check
SITE_REPO = REPO / "tests" / "fixtures" / "design_review_web" / "site_repo"
FLEET = REPO.parent

interp = None
for sibling in ("project-scaffolding", "app-launcher"):
    cand = capture.resolve_interpreter(plan.Target(name=sibling, root=FLEET / sibling, base_url="file:///"))
    if cand["status"] == "ok":
        interp = cand["python"]
        break

if interp is None:
    _h.skip("web-page browser leg: no sibling .venv with Playwright (project-scaffolding, app-launcher) -- NOT verified")
else:
    rb = rubric_mod.load_rubric(measure_web.WEB_RUBRIC)
    tokens = measure_web.load_tokens(measure_web.WEB_SPEC)
    target = plan.Target(name="web-fixture", root=SITE_REPO, base_url="static-preview:pending")
    run_dir = throwaway_dir("design-review-web-run-")
    pages = ["/", "/squeeze", "/menu", "/carousel"]
    doc = measure_web.measure_web(target, rb, tokens, list(measure_web.DEVICES), run_dir, pages=pages, python_override=interp)
    screens = {s["id"]: s for s in doc["screens"]}

    def metric(sid: str, path: str) -> object:
        s = screens.get(sid) or {}
        node: object = s.get("metrics") or {}
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return node

    check(doc["unmeasured"] is None, f"the fixture site is measured: {doc['unmeasured']}")
    check(doc["preview"]["source"] == "scripts/serve.py", f"the repo's own preview script is found: {doc.get('preview')}")
    check(len(screens) == 12 and all(s["status"] == "ok" for s in screens.values()),
          f"4 pages x 3 viewports measured: {[(k, s.get('reason')) for k, s in screens.items() if s['status'] != 'ok']}")
    check(capture.probe_listening(str(doc["base_url"]), timeout=3)["status"] != "listening", "the preview it started is stopped afterwards")
    check(all(Path(str(s.get("screenshot"))).is_file() for s in screens.values()), "a full-page screenshot per page and viewport")

    # The 152px text column at 800px.
    squeezed = metric("tablet-light-squeeze", "page.text_column_squeezed") or []
    check(metric("tablet-light-squeeze", "page.text_column_squeezed_count") == 1 and "152px" in str(squeezed),
          f"/squeeze at 800px: the text column gets 152px of the row: {squeezed}")
    check(metric("tablet-light-home", "page.text_column_squeezed_count") == 0, "the home page's min() media role shares the row evenly")

    # The invisible menu panel: every link fails the hit test, though each has a box.
    misses = metric("phone-light-menu", "page.menu_hit_miss_count")
    check(misses == 5, f"/menu on a phone: all 5 links miss their own centre ({misses}: {metric('phone-light-menu', 'page.menu_hit_miss')})")
    check(any("backdrop-filter" in str(x) for x in metric("phone-light-menu", "page.header_contains_fixed") or []),
          "/menu: the header names its backdrop-filter")
    check(metric("phone-light-home", "page.menu_hit_miss_count") == 0, "the home page's phone menu passes the hit test")

    # A partial carousel card on a desktop.
    check(metric("desktop-light-carousel", "page.carousel_partial_card_count") == 1,
          f"/carousel at 1440px: the fixed-width cards leave a partial card ({metric('desktop-light-carousel', 'page.carousel_partial_card')})")
    check(metric("desktop-light-home", "page.carousel_partial_card_count") == 0, "the home page shows 3 whole cards at 1440px")
    check(metric("phone-light-home", "page.carousel_partial_card_count") == 0, "the home page shows 1 card plus the next card's edge on a phone")
    step = metric("desktop-light-home", "page.carousel_step_error_px")
    check(isinstance(step, (int, float)) and step <= 2, f"one arrow click moves one card plus the gap, arrows centred ({step})")

    # Search facts and the cold load, on the good page.
    for k in ("title_fail", "description_fail", "canonical_fail", "social_card_fail", "structured_data_fail", "redirect_fail",
              "robots_fail", "soft_404", "sitemap_mismatch"):
        check(metric("phone-light-home", f"seo.{k}") == [], f"home seo.{k} passes: {metric('phone-light-home', f'seo.{k}')}")
    lcp = metric("phone-light-home", "perf.lcp_ms")
    check(isinstance(lcp, (int, float)) and lcp > 0, f"a throttled cold load reports LCP ({lcp})")
    check(metric("phone-light-home", "perf.budget_over") == [], f"the home page is inside its budget: {metric('phone-light-home', 'perf.load')}")
    check(metric("desktop-light-home", "a11y.focus_ring_missing_count") == 0, "every focused element draws the browser's ring")

_h.report_and_exit("test_design_review_web_browser", skip_code=SKIP_EXIT)
