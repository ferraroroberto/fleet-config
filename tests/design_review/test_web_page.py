"""Unit tests for the web-page leg of design_review: the section 3 pass rules on known-bad facts, the search
facts against a stubbed preview, preview resolution, and a whole metrics document scored by design.web.rubric.toml.

The browser leg (the same rules measured in a real page) is `tests/design_review_web_browser/`.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_review/test_web_page.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import json
import struct

from _suite import REPO, _h, check  # noqa: E402
from throwaway_dir import throwaway_dir  # noqa: E402

from design_review import evaluate, report, rubric as rubric_mod  # noqa: E402
from design_review.web import checks, measure_web, preview, seo  # noqa: E402

WEB_RUBRIC = REPO / "design.web.rubric.toml"
WEB_SPEC = REPO / "design.web.md"
rb = rubric_mod.load_rubric(WEB_RUBRIC)
tokens = measure_web.load_tokens(WEB_SPEC)
limits = checks.limits_from_spec(tokens)

# ---- the rubric and the measurement agree on every metric name ---------------

check(rubric_mod.check_metric_names(rb, list(measure_web.METRICS)) == [],
      f"every design.web.rubric.toml metric is one measure_web produces: {rubric_mod.check_metric_names(rb, list(measure_web.METRICS))}")
check(any(r.id == "WEB-LAYOUT-06" for r in rb.rules), "the portrait-row rule is in the rubric")
check(limits["budgets"]["product"] == {"bytes": 300 * 1024, "requests": 15}, f"budgets read from the spec: {limits['budgets'].get('product')}")
check(limits["font_files_max"] == 4 and limits["font_bytes_max"] == 150 * 1024, "font limits read from the spec")

# ---- phone reading order ------------------------------------------------------


def block(key: str, kind: str, top: int, bottom: int, left: int = 0, right: int = 358, group: str = "1:0") -> dict:
    return {"key": key, "group": group, "kind": kind, "top": top, "bottom": bottom, "left": left, "right": right, "text": f"{kind} {key}"}


phone = [block("a", "title", 0, 40), block("b", "picture", 50, 300), block("c", "text", 310, 400)]
desk_beside_right = [block("a", "title", 0, 40, 0, 500), block("b", "picture", 50, 520, 600, 1120), block("c", "text", 60, 200, 0, 500)]
desk_beside_left = [block("a", "title", 0, 40, 600, 1120), block("b", "picture", 50, 520, 0, 520), block("c", "text", 60, 200, 600, 1120)]
desk_above = [block("a", "title", 0, 40), block("b", "picture", 50, 300), block("c", "text", 320, 400)]
check(len(checks.phone_order_violations(phone, desk_beside_right) or []) == 1,
      "phone order: a picture read before text the desktop shows on its left fails")
check(checks.phone_order_violations(phone, desk_beside_left) == [], "phone order: the desktop shows the picture first (left) -> pass")
check(checks.phone_order_violations(phone, desk_above) == [], "phone order: the desktop shows the picture above the text -> pass")
check(checks.phone_order_violations([block("a", "text", 0, 40), block("b", "picture", 50, 90), block("c", "action", 100, 140)],
                                    desk_beside_right) == [], "phone order: title/text, picture, then action passes")
check(checks.phone_order_violations(phone, None) is None, "phone order with no desktop measurement is unknown, never a pass")
other_row = [block("b", "picture", 50, 300, group="1:0"), block("c", "text", 310, 400, group="1:1")]
check(checks.phone_order_violations(other_row, desk_beside_right) == [], "phone order compares blocks of one row only")

# ---- the tablet squeeze, image roles, portraits --------------------------------

squeezed = [{"row_w": 696, "text_w": 152, "media_w": 520, "text": "div.col"}]
check(len(checks.text_squeezed(squeezed)) == 1, "the 152px text column of a 696px row at 800px fails")
check(checks.text_squeezed([{"row_w": 696, "text_w": 336, "media_w": 336, "text": "div.col"}]) == [], "an even split passes")
spread, roles = checks.image_role_spread([{"role": "tile", "w": 300}, {"role": "tile", "w": 296}, {"role": "thumb", "w": 96}])
check(spread == 4 and roles == ["tile"], f"image roles: tiles 4px apart fail, named by role ({spread}, {roles})")
check(checks.image_role_spread([{"role": "tile", "w": 300}, {"role": "tile", "w": 299}])[0] <= checks.ROLE_TOLERANCE_PX, "1px apart is within ±2px")
good_portraits = [{"src": "a", "w": 160, "h": 160, "radius": "50%", "top": 100, "name_top": 102},
                  {"src": "b", "w": 160, "h": 160, "radius": "50%", "top": 400, "name_top": 400}]
check(checks.portrait_misaligned(good_portraits) == [], "portraits of one size, radius and top pass")
bad_portraits = good_portraits + [{"src": "c", "w": 150, "h": 150, "radius": "12px", "top": 700, "name_top": 690}]
check(len(checks.portrait_misaligned(bad_portraits)) == 1 and "radius" in checks.portrait_misaligned(bad_portraits)[0],
      "a smaller, square-cornered portrait 10px off its name fails once, with every reason")

# ---- carousels ---------------------------------------------------------------


def carousel(cards, left=160, right=1280, scrolls=True, nav=720.0, index=0):
    return {"index": index, "left": left, "right": right, "top": 0, "scrolls": scrolls, "gap": 24, "nav_centre": nav,
            "cards": [{"left": a, "right": b, "top": 0, "w": b - a} for a, b in cards]}


whole3 = carousel([(160, 517), (541, 899), (923, 1280), (1304, 1661)])
partial = carousel([(160, 560), (584, 984), (1008, 1408)])
check(checks.carousel_partial([whole3], 1440) == [], "desktop: 3 whole cards, the 4th off-screen, passes")
check(len(checks.carousel_partial([partial], 1440)) == 1, "desktop: a partial third card fails")
grid = carousel([(160, 517), (541, 899), (923, 1280)] * 1, scrolls=False)
grid["cards"] += [{"left": a, "right": b, "top": 400, "w": b - a} for a, b in [(160, 517), (541, 899), (923, 1280)]]
check(checks.carousel_partial([grid], 1440) == [], "desktop: a non-scrolling 3x2 testimonial grid passes")
phone_ok = carousel([(16, 310), (326, 619), (635, 929)], left=0, right=390)
phone_bad = carousel([(16, 374), (390, 748)], left=0, right=390)
check(checks.carousel_partial([phone_ok], 390) == [], "phone: one whole card plus the next card's edge passes")
check(len(checks.carousel_partial([phone_bad], 390)) == 1, "phone: one card with no next edge showing fails")
err, _items = checks.carousel_step_error([whole3], [{"index": 0, "step": 381, "expected": 381.33}])
check(err <= 2, f"one click of one card plus the gap, arrows centred, passes ({err})")
err, _items = checks.carousel_step_error([carousel(whole3["cards"] and [(160, 517)], nav=760.0)], [{"index": 0, "step": 762, "expected": 381}])
check(err > 300, f"a click that moves two cards fails by the overshoot ({err})")

# ---- menu hit test, footer contrast, budgets, fonts, delivery ----------------

misses, why = checks.menu_misses({"toggle": True, "nav": True, "links": [{"href": "/", "hit": False}, {"href": "/a", "hit": False}]})
check(misses is not None and len(misses) == 2 and why is None, "an invisible menu panel: every link misses its own centre")
check(checks.menu_misses({"toggle": True, "nav": True, "links": [{"href": "/", "hit": True}]})[0] == [], "a reachable menu passes")
check(checks.menu_misses({"toggle": True, "nav": True, "links": []})[0] is None, "a menu with no links is unknown, never a pass")
check(checks.menu_misses({"error": "boom"})[0] is None, "a failed menu check is unknown")
check(len(checks.footer_contrast_fails([{"text": "p", "ratio": 6.2, "floor": 7}, {"text": "strong", "ratio": 6.0, "floor": 4.5}])) == 1,
      "footer: body text at 6.2:1 fails the 7:1 band floor; a label at 6:1 passes 4.5:1")
perf = {"bytes": 400 * 1024, "requests": 22, "html_bytes": 10_000, "first_party_js_bytes": 4000, "font_files": [], "font_faces": []}
check(checks.budget_over(perf, "landing", limits) == [], "400 KB in 22 requests is inside the landing budget")
check(len(checks.budget_over(perf, "product", limits) or []) == 2, "the same load is over the product budget on bytes and requests")
check(checks.budget_over(perf, "no-such-type", limits) is None, "an unknown page type has no budget: unknown, never a pass")
fonts = {"font_files": [{"url": f"f{i}.woff2", "bytes": 40_000} for i in range(5)] + [{"url": "x.ttf", "bytes": 1}],
         "font_faces": [{"family": "X", "display": "auto"}]}
ff = checks.font_fail(fonts, limits)
check(any("6 font files" in f for f in ff) and any("not WOFF2" in f for f in ff) and any("font-display auto" in f for f in ff)
      and any("KB of fonts" in f for f in ff), f"font failures named: {ff}")
check(len(checks.delivery_fails(None)) == 1, "no _headers fails delivery")
check(checks.delivery_fails("/_vendored/*\n  Cache-Control: public, max-age=31536000, immutable\n") == [], "a year of immutable caching passes")
check(len(checks.delivery_fails("/*\n  Cache-Control: max-age=600\n")) == 1, "ten minutes is not long-lived caching")

# ---- search facts against a stubbed preview ----------------------------------


def jpeg(w: int, h: int) -> bytes:
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"JF" + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, h, w) + b"\x00" * 6


def page_html(title: str, desc: str, canonical: str, extra: str = "", og_image: str = "https://ex.org/og.jpg") -> bytes:
    return f"""<html lang="en"><head><title>{title}</title><meta name="description" content="{desc}">
<link rel="canonical" href="{canonical}"><meta property="og:title" content="t"><meta property="og:description" content="d">
<meta property="og:url" content="{canonical}"><meta property="og:type" content="website"><meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary_large_image">{extra}</head><body><a href="/about">About</a><a href="/gone">Gone</a>
<a href="https://other.org/x">x</a><a href="mailto:a@b.c">m</a></body></html>""".encode()


LONG_DESC = "A description long enough to sit inside the seventy to one hundred and sixty character range."
SITE = {
    "/": (200, page_html("Home of the example site, and its work", LONG_DESC, "https://ex.org/",
                         '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Person","name":"Ex"}</script>')),
    "/about": (200, page_html("Short", LONG_DESC, "https://ex.org/elsewhere", og_image="/og-small.jpg")),
    "/sitemap.xml": (200, b"<urlset><url><loc>https://ex.org/</loc></url><url><loc>https://ex.org/about</loc></url></urlset>"),
    "/robots.txt": (200, b"User-agent: *\nAllow: /\nSitemap: https://ex.org/sitemap.xml\n"),
    "/og.jpg": (200, jpeg(1200, 630)),
    "/og-small.jpg": (200, jpeg(1000, 149)),
    "/old": (301, b""), "/temp": (302, b""),
}


def fake_fetch(path: str) -> seo.Response:
    if path in SITE:
        status, body = SITE[path]
        headers = {"location": "/"} if status in (301, 302) else {}
        return seo.Response(status, headers, body)
    return seo.Response(404, {}, b"not found")


site_dir = throwaway_dir("web-seo-site-")
(site_dir / "index.html").write_text("x", encoding="utf-8")
(site_dir / "about.html").write_text("x", encoding="utf-8")
(site_dir / "extra.html").write_text("x", encoding="utf-8")
(site_dir / "404.html").write_text("x", encoding="utf-8")
(site_dir / "_redirects").write_text("/old / 301\n/temp / 302\n/blog/* /posts/:splat 301\n", encoding="utf-8")
SITE["/extra"] = (200, page_html("The extra page of the example site here", LONG_DESC, "https://ex.org/extra"))
doc = seo.measure_site(fake_fetch, ["/", "/about"], site_dir, tokens, {})
home, about, site = doc["pages"]["/"], doc["pages"]["/about"], doc["site"]
check(home["title_fail"] == [] and home["canonical_fail"] == [] and home["social_card_fail"] == [] and home["structured_data_fail"] == [],
      f"a complete home page passes its search facts: {home}")
check(home["kind"] == "home", "the root is the home page")
check(any("characters" in f for f in about["title_fail"]), "a 5-character title fails the 30-60 range")
check(any("shared with" in f for f in about["description_fail"]), "a description shared with another URL fails")
check(about["canonical_fail"] == ["canonical points to /elsewhere, not /about"], f"a canonical pointing elsewhere fails: {about['canonical_fail']}")
check(any("not absolute" in f for f in about["social_card_fail"]) and any("1000x149" in f for f in about["social_card_fail"]),
      f"a relative, 1000x149 og:image fails: {about['social_card_fail']}")
check(home["broken_internal_link"] == ["/gone (404)"], f"the one broken internal link is named, external and mail links ignored: {home['broken_internal_link']}")
check(any("/extra is missing from the sitemap" in f for f in site["sitemap_mismatch"]), f"an indexable page missing from the sitemap: {site['sitemap_mismatch']}")
check(any("declares another canonical" in f for f in site["sitemap_mismatch"]), "a listed URL canonicalised elsewhere is a mismatch")
check(site["robots_fail"] == [], "an allowing robots.txt with a Sitemap line passes")
check(site["redirect_fail"] == ["/temp answers 302, not 301"], f"a 302 for a moved URL fails, the splat rule is skipped: {site['redirect_fail']}")
check(site["soft_404"] == [], "a missing URL answering 404 passes")
check(seo.image_size(jpeg(1200, 630)) == (1200, 630), "JPEG size read from its SOF header")
check(seo.structured_data_findings(seo.parse_page(b'<script type="application/ld+json">{"@type":"Product"}</script>'), "product")
      == ["Product lacks name, offers"], "a Product without name and offers does not validate")
check(seo.structured_data_findings(seo.parse_page(b'<script type="application/ld+json">{oops</script>'), "landing")[0].startswith("JSON-LD does not parse"),
      "unparseable JSON-LD is named")
check(seo.page_kind("/shop/p/one", seo.PageFacts(), {"/shop/p/*": "product"}) == "product", "a declared page-type glob wins")
check(seo.budget_type("home") == "landing", "the home page is held to the landing budget")

# ---- preview resolution ------------------------------------------------------

repo = throwaway_dir("web-preview-repo-")
try:
    preview.resolve_preview(repo, {})
    check(False, "a repo with no preview must not resolve")
except preview.PreviewError as exc:
    check(exc.reason == "NO_PREVIEW", f"no preview script -> NO_PREVIEW ({exc.reason})")
(repo / "scripts").mkdir()
(repo / "scripts" / "serve.py").write_text("", encoding="utf-8")
(repo / "site").mkdir()
(repo / "site" / "index.html").write_text("x", encoding="utf-8")
p_old = preview.resolve_preview(repo, {})
check(p_old.source == "scripts/serve.py" and p_old.site_dir == repo / "site", "the older serve.py is found, site/ is the built folder")
(repo / "scripts" / "site_serve.py").write_text("", encoding="utf-8")
check(preview.resolve_preview(repo, {}).source == "scripts/site_serve.py", "the vendored site_serve.py wins over serve.py")
declared = preview.resolve_preview(repo, {"preview": {"command": ["scripts/serve.py", "--root", "site"], "site_dir": "site"}})
check(declared.source == "declared" and declared.argv[-2:] == ["--port", "{port}"], f"a declared command gets the port appended: {declared.argv}")
for bad in ({"preview": {"command": ["../outside.py"]}}, {"preview": {"command": []}}, {"preview": "x"}):
    try:
        preview.resolve_preview(repo, bad)
        check(False, f"bad preview block {bad} must be refused")
    except preview.PreviewError as exc:
        check(exc.reason == "BAD_PREVIEW", f"{bad} -> BAD_PREVIEW")
check(preview.free_port() > 0, "a free loopback port is found")

# ---- a whole document: raw facts -> metrics -> the rubric's verdict ----------


def raw_device(device: str, menu_hits: bool = True, squeeze: bool = False) -> dict:
    text = {k: [] for k in ("under_label", "body_under_floor", "measure_over", "leading_under_floor", "low_contrast",
                            "not_start_aligned", "heading_smaller_than_body")}
    text.update({"runs": 20, "min_px": 13})
    rec = {"status": 200, "fold_eager": [], "screenshot": None, "carousel_steps": [],
           "focus": {"focused": 10, "ring_missing": [], "obscured": []},
           "measure": {"text": text, "footer_runs": [{"text": "p", "ratio": 7.1, "floor": 7}],
                       "targets": {"total": 20, "small": [], "control_under_height": []},
                       "a11y": {"landmarks_missing": [], "img_missing_alt": [], "field_unlabelled": [], "motion": []},
                       "layout": {"scroll_w": 390, "inner_w": 390},
                       "page": {"h1_count": 1, "heading_skip": [], "primary": [{"section": 0, "primary": 1}], "header_contains_fixed": [],
                                "contact_route_missing": False, "vague_link_text": [], "eager_embed": [],
                                "blocks": [block("a", "title", 0, 40), block("c", "text", 50, 90), block("b", "picture", 100, 300)],
                                "media_rows": squeezed if squeeze else [], "role_images": [], "portraits": [], "carousels": []},
                       "images_unoptimised": []}}
    if device == "phone":
        rec["menu"] = {"toggle": True, "nav": True, "links": [{"href": "/", "hit": menu_hits}]}
    if device == "desktop":
        rec["sticky"] = {"sticky": 1, "blank_max": 150}
    return rec


cold = {"lcp_ms": 1800, "cls": 0.01, "tbt_ms": 20, "bytes": 200_000, "requests": 12, "html_bytes": 9000,
        "first_party_js_bytes": 3000, "font_files": [{"url": "a.woff2", "bytes": 9000}], "font_faces": [], "render_blocking_third_party": []}
raw = {"pages": {"/": {"devices": {"phone": raw_device("phone"), "tablet": raw_device("tablet"), "desktop": raw_device("desktop")}, "perf": cold},
                 "/bad": {"devices": {"phone": raw_device("phone", menu_hits=False), "tablet": raw_device("tablet", squeeze=True),
                                      "desktop": raw_device("desktop")}, "perf": {"error": "TimeoutError: never loaded"}}}}
site_ok = {"sitemap_mismatch": [], "robots_fail": [], "redirect_fail": [], "soft_404": []}
page_ok = {"kind": "landing", **{k: [] for k in measure_web.SEO_PAGE}, "broken_internal_link": []}
seo_doc = {"site": site_ok, "pages": {"/": page_ok, "/bad": page_ok}}
screens = measure_web.build_screens(raw, ["/", "/bad"], list(measure_web.DEVICES), seo_doc, limits,
                                    ["no _headers file: static assets get the host's default max-age=0"])
check([s["status"] for s in screens] == ["ok"] * 6, f"six screens built: {[(s['id'], s.get('reason')) for s in screens]}")
check(screens[0]["id"] == "phone-light-home" and screens[3]["view"] == "/bad", "screen ids and views name the page")
metrics_doc = {"schema_version": 1, "target": "fixture", "surface": "web-page", "spec_user_scope": "not_confirmed",
               "screens": screens, "rubric_version": rb.version}
ev = evaluate.evaluate(metrics_doc, rb, rubric_mod.load_specs(WEB_SPEC, WEB_SPEC))
by = {r["id"]: r for r in ev["rules"]}
check(by["WEB-TOUCH-03"]["status"] == "fail" and by["WEB-TOUCH-03"]["evidence"][0]["screen"] == "phone-light-bad",
      "the menu miss fails WEB-TOUCH-03 on the bad page's phone screen")
check(by["WEB-LAYOUT-03"]["status"] == "fail" and by["WEB-LAYOUT-03"]["evidence"][0]["screen"] == "tablet-light-bad",
      "the tablet squeeze fails WEB-LAYOUT-03")
check(by["WEB-LAYOUT-02"]["status"] == "pass", "title, text, picture passes the phone order")
check(by["WEB-PERF-04"]["status"] == "unmeasured" and "lighthouse" in by["WEB-PERF-04"]["reason"],
      f"Lighthouse is unmeasured with its reason, never a pass: {by['WEB-PERF-04']['reason']}")
check(by["WEB-PERF-01"]["status"] == "unmeasured" and "cold load" in by["WEB-PERF-01"]["reason"],
      f"a failed cold load leaves LCP unmeasured, never passed: {by['WEB-PERF-01']['reason']}")
check(by["WEB-PERF-10"]["status"] == "fail", "no _headers fails delivery on every screen")
check(by["WEB-LAYOUT-05"]["status"] == "pass", "a 150px blank stretch beside the text passes")
check(ev.get("surface") == "web-page" and ev.get("spec_user_scope") == "not_confirmed", "the surface and the spec copy's state ride through")
sentence = report.finding_sentence(by["WEB-TOUCH-03"], ev)
check("/bad" in sentence and "{page}" not in sentence, f"{{page}} fills with the screen's URL: {sentence}")
html = report.render_report(ev)
check("WEB-TOUCH-03" in html, "the report renders the web-page findings")

# The web leg reads its rubric and spec from the metrics document when no flag names them.
run = throwaway_dir("web-eval-")
(run / "metrics.json").write_text(json.dumps({**metrics_doc, "rubric_path": str(WEB_RUBRIC), "spec_path": str(WEB_SPEC)}), encoding="utf-8")
from design_review import cli  # noqa: E402
rc = cli.main(["evaluate", str(run / "metrics.json"), "--out", str(run / "evaluate.json")])
out = json.loads((run / "evaluate.json").read_text(encoding="utf-8"))
check(rc == 0 and out["rubric_version"] == rb.version and any(r["id"] == "WEB-TOUCH-03" for r in out["rules"]),
      "evaluate scores a web-page metrics.json with design.web.rubric.toml without flags")

_h.report_and_exit("test_web_page")
