"""Stage 2 of /design-review for a web page: start the static preview, walk it, write `metrics.json`.

The web-app measure (`capture.measure_target`) walks a running app's tabs;
a web page has no app. This one:

  1. resolves the target (name, checkout, `[design.review]`) through `plan.resolve_target`;
  2. resolves the page list: `--pages a,b`, `--pages all` (every sitemap URL), or by default
     the `key views` of the repo's `## UX surface` block (else `/`);
  3. starts the repo's own preview on a free loopback port (`preview.py`), under the target's
     interpreter, and stops that PID when done, whatever happened;
  4. runs `page_walk.py` under the same interpreter (Playwright lives there) at 390, 800 and 1440 px;
  5. reads the search facts over HTTP (`seo.py`) and `_headers` from the built site;
  6. turns the raw facts into rubric metrics with `checks.py`, one screen per page and viewport.

The document has the web-app shape (`screens[]` with `id/device/theme/view/kind/status/metrics`), so
`evaluate.evaluate` and `report.render_report` score and render it unchanged with
`design.web.rubric.toml`. It also records `surface = "web-page"` and the rubric and spec it was
measured against, so `evaluate` and `render` pick them up without flags.

The spec is read from this repo's `design.web.md`. The user-scope copy `~/.claude/design.web.md`
(linked by `install.ps1`) is reported separately as `spec_user_scope`: `present` or `not_confirmed`
when the link is missing, never folded into a pass.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import ux_surface  # noqa: E402
from design_lint.spec import parse_spec  # noqa: E402
from fleet_core import runtime_files  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402

from .. import capture, measure, plan  # noqa: E402
from ..rubric import Rubric, resolve_params  # noqa: E402
from . import checks, preview, seo  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
WEB_RUBRIC = REPO_ROOT / "design.web.rubric.toml"
WEB_SPEC = REPO_ROOT / "design.web.md"
USER_SPEC = Path.home() / ".claude" / "design.web.md"
PAGE_WALK = Path(__file__).resolve().parent / "page_walk.py"
DEVICES = ("phone", "tablet", "desktop")
VIEWPORT_W = {"phone": 390, "tablet": 800, "desktop": 1440}
WALK_TIMEOUT_S = 1800.0
THEME = "light"  # a web page is light by default; a site's optional dark theme is not part of the contract
LIGHTHOUSE_UNMEASURED = "the lighthouse CLI is not run by this review (not installed); scores are not confirmed"
SURFACE = "web-page"


# Every metric a web-page screen carries, for rubric validation (`rubric.check_metric_names`).
METRICS = (
    "text.min_px", "text.body_under_floor_count", "text.measure_over_count", "text.leading_under_floor_count",
    "text.low_contrast_count", "text.not_start_aligned_count", "text.heading_smaller_than_body_count",
    "targets.small_count", "targets.control_under_height_count",
    "a11y.focus_ring_missing_count", "a11y.focus_obscured_count",
    "layout.overflow_x",
    "page.h1_count", "page.heading_skip_count", "page.primary_buttons_per_section_max", "page.footer_contrast_fail_count",
    "page.menu_hit_miss_count", "page.img_missing_alt_count", "page.landmarks_missing", "page.motion_violation_count",
    "page.field_unlabelled_count", "page.phone_order_violation_count", "page.text_column_squeezed_count",
    "page.image_role_width_spread_px", "page.sticky_blank_max_px", "page.portrait_misaligned_count",
    "page.carousel_partial_card_count", "page.carousel_step_error_px", "page.eager_embed_count",
    "page.header_contains_fixed", "page.contact_route_missing", "page.vague_link_text_count",
    "seo.title_fail", "seo.description_fail", "seo.canonical_fail", "seo.social_card_fail", "seo.structured_data_fail",
    "seo.noindex_on_indexable", "seo.broken_internal_link_count", "seo.sitemap_mismatch_count", "seo.robots_fail",
    "seo.redirect_fail_count", "seo.soft_404",
    "perf.lcp_ms", "perf.cls", "perf.tbt_ms", "perf.lighthouse_min", "perf.budget_over", "perf.image_unoptimised_count",
    "perf.eager_below_fold_count", "perf.render_blocking_third_party_count", "perf.font_fail", "perf.delivery_fail_count",
)


def spec_user_scope() -> str:
    return "present" if USER_SPEC.is_file() else "not_confirmed"


def page_slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "home"


def device_list(names: Optional[List[str]]) -> List[str]:
    if not names:
        return list(DEVICES)
    unknown = [n for n in names if n not in DEVICES]
    if unknown:
        raise plan.PlanError(f"unknown web-page viewport(s) {unknown}; known: {list(DEVICES)}")
    return list(names)


def default_pages(root: Optional[Path]) -> List[str]:
    """The `key views` of the repo's `## UX surface` block, path first (`/x  (note)` -> `/x`), else `/`."""
    block = ux_surface.parse_ux_surface_block((root / "CLAUDE.md").read_text(encoding="utf-8")) \
        if root and (root / "CLAUDE.md").is_file() else None
    views = [str(v).split()[0] for v in (block or {}).get("key_views") or [] if str(v).strip().startswith("/")]
    return views or ["/"]


def declared_page_types(review: Dict[str, object]) -> Dict[str, str]:
    raw = review.get("page_types") if isinstance(review, dict) else None
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _listed(section: Dict[str, object], name: str, items: object) -> None:
    """`name` (the findings) and `name_count`; an `{"error": ...}` stays an error on both."""
    if isinstance(items, dict) and "error" in items:
        section[name] = items
        section[f"{name}_count"] = items
        return
    items = list(items or [])
    section[name] = items
    section[f"{name}_count"] = len(items)


def _err(reason: str) -> Dict[str, str]:
    return {"error": reason}


def screen_metrics(path: str, device: str, raw_page: Dict[str, object], seo_doc: Dict[str, object],
                   limits: Dict[str, object], delivery: List[str]) -> Dict[str, object]:
    """One page at one viewport: the raw walk facts and search facts as the rubric's metrics."""
    devices = raw_page["devices"]  # type: ignore[index]
    rec = devices[device]
    m = rec["measure"]
    t, pg = m["text"], m["page"]
    text: Dict[str, object] = {"runs": t["runs"], "min_px": t["min_px"], "under11": t["under_label"]}
    for k in ("body_under_floor", "measure_over", "leading_under_floor", "low_contrast", "not_start_aligned",
              "heading_smaller_than_body"):
        _listed(text, k, t[k])
    targets: Dict[str, object] = {"total": m["targets"]["total"]}
    _listed(targets, "small", m["targets"]["small"])
    _listed(targets, "control_under_height", m["targets"]["control_under_height"])
    focus = rec.get("focus")
    a11y: Dict[str, object] = {}
    if isinstance(focus, dict) and "error" not in focus:
        _listed(a11y, "focus_ring_missing", focus["ring_missing"])
        _listed(a11y, "focus_obscured", focus["obscured"])
    else:
        why = _err(f"focus walk: {focus['error']}" if isinstance(focus, dict) else "focus walk did not run")
        _listed(a11y, "focus_ring_missing", why)
        _listed(a11y, "focus_obscured", why)
    lay = m["layout"]
    layout = {"overflow_x": max(0, int(lay["scroll_w"]) - int(lay["inner_w"])), "scroll_w": lay["scroll_w"], "inner_w": lay["inner_w"]}

    page: Dict[str, object] = {"h1_count": pg["h1_count"], "header_contains_fixed": pg["header_contains_fixed"],
                               "contact_route_missing": pg["contact_route_missing"], "landmarks_missing": m["a11y"]["landmarks_missing"],
                               "primary_buttons_per_section_max": max([s["primary"] for s in pg["primary"]] or [0])}
    _listed(page, "heading_skip", pg["heading_skip"])
    _listed(page, "img_missing_alt", m["a11y"]["img_missing_alt"])
    _listed(page, "motion_violation", m["a11y"]["motion"])
    _listed(page, "field_unlabelled", m["a11y"]["field_unlabelled"])
    _listed(page, "vague_link_text", pg["vague_link_text"])
    _listed(page, "eager_embed", pg["eager_embed"])
    _listed(page, "footer_contrast_fail", checks.footer_contrast_fails(m["footer_runs"]))
    _listed(page, "text_column_squeezed", checks.text_squeezed(pg["media_rows"]))
    _listed(page, "portrait_misaligned", checks.portrait_misaligned(pg["portraits"]))
    _listed(page, "carousel_partial_card", checks.carousel_partial(pg["carousels"], VIEWPORT_W[device]))
    spread, roles = checks.image_role_spread(pg["role_images"])
    page["image_role_width_spread_px"], page["image_role_width_spread_px_items"] = spread, roles
    steps = rec.get("carousel_steps")
    if isinstance(steps, dict):
        page["carousel_step_error_px"] = _err(f"carousel check: {steps['error']}")
    else:
        page["carousel_step_error_px"], page["carousel_step_error_px_items"] = checks.carousel_step_error(pg["carousels"], steps or [])
    if device == "phone":
        misses, why_not = checks.menu_misses(rec.get("menu"))
        _listed(page, "menu_hit_miss", misses if misses is not None else _err(str(why_not)))
        desk = devices.get("desktop")
        desk_blocks = desk["measure"]["page"]["blocks"] if isinstance(desk, dict) and "measure" in desk \
            and "error" not in desk["measure"] else None
        order = checks.phone_order_violations(pg["blocks"], desk_blocks)
        _listed(page, "phone_order_violation", order if order is not None else _err("no desktop measurement to compare the order with"))
    if device == "desktop":
        sticky = rec.get("sticky")
        if isinstance(sticky, dict) and "error" not in sticky:
            page["sticky_blank_max_px"] = sticky["blank_max"]
            page["sticky_blank_max_px_items"] = [f"{sticky['sticky']} sticky picture(s) scanned"]
        else:
            page["sticky_blank_max_px"] = _err(f"sticky scan: {sticky['error']}" if isinstance(sticky, dict) else "sticky scan did not run")

    return {"text": text, "targets": targets, "a11y": a11y, "layout": layout, "page": page,
            "seo": seo_metrics(seo_doc, path),
            "perf": perf_metrics(raw_page, rec, device, limits, delivery, seo_doc, path)}


SEO_PAGE = ("title_fail", "description_fail", "canonical_fail", "social_card_fail", "structured_data_fail", "noindex_on_indexable")
SEO_SITE = ("robots_fail", "soft_404")


def seo_metrics(seo_doc: Dict[str, object], path: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    if "error" in seo_doc:
        for k in SEO_PAGE + SEO_SITE + ("broken_internal_link", "sitemap_mismatch", "redirect_fail"):
            out[k] = _err(str(seo_doc["error"]))
        out.update({f"{k}_count": out[k] for k in ("broken_internal_link", "sitemap_mismatch", "redirect_fail")})
        return out
    page = seo_doc["pages"].get(path) or _err("not crawled")  # type: ignore[union-attr]
    site = seo_doc["site"]
    for k in SEO_PAGE:
        out[k] = page[k] if "error" not in page else page
    _listed(out, "broken_internal_link", page["broken_internal_link"] if "error" not in page else page)
    for k in SEO_SITE:
        out[k] = site[k]  # type: ignore[index]
    _listed(out, "sitemap_mismatch", site["sitemap_mismatch"])  # type: ignore[index]
    _listed(out, "redirect_fail", site["redirect_fail"])  # type: ignore[index]
    return out


def perf_metrics(raw_page: Dict[str, object], rec: Dict[str, object], device: str, limits: Dict[str, object],
                 delivery: List[str], seo_doc: Dict[str, object], path: str) -> Dict[str, object]:
    perf = raw_page.get("perf")
    out: Dict[str, object] = {"lighthouse_min": _err(LIGHTHOUSE_UNMEASURED)}
    _listed(out, "image_unoptimised", rec["measure"]["images_unoptimised"])  # type: ignore[index]
    _listed(out, "eager_below_fold", rec.get("fold_eager"))
    _listed(out, "delivery_fail", delivery)
    if not isinstance(perf, dict) or "error" in perf:
        why = _err(f"cold load: {perf['error']}" if isinstance(perf, dict) else "cold load not run")
        for k in ("lcp_ms", "cls", "tbt_ms", "budget_over", "font_fail"):
            out[k] = why
        _listed(out, "render_blocking_third_party", why)
        return out
    _listed(out, "render_blocking_third_party", perf["render_blocking_third_party"])
    out["font_fail"] = checks.font_fail(perf, limits)
    if device == "phone":
        out["lcp_ms"] = perf["lcp_ms"] if perf.get("lcp_ms") is not None else _err("no largest-contentful-paint entry")
        out["cls"], out["tbt_ms"] = perf["cls"], perf["tbt_ms"]
        kind = (seo_doc.get("pages") or {}).get(path, {}).get("kind", "landing") if "error" not in seo_doc else "landing"  # type: ignore[union-attr]
        over = checks.budget_over(perf, seo.budget_type(str(kind)), limits)
        out["budget_over"] = over if over is not None else _err(f"no budget for page type {kind!r} in design.web.md")
        out["load"] = {k: perf.get(k) for k in ("bytes", "requests", "html_bytes", "fcp_ms", "first_party_js_bytes")}
        out["page_type"] = kind
    return out


def build_screens(raw: Dict[str, object], pages: List[str], devices: List[str], seo_doc: Dict[str, object],
                  limits: Dict[str, object], delivery: List[str]) -> List[Dict[str, object]]:
    screens: List[Dict[str, object]] = []
    for path in pages:
        raw_page = (raw.get("pages") or {}).get(path)  # type: ignore[union-attr]
        for device in devices:
            base = {"id": plan.screen_id(device, THEME, page_slug(path)), "device": device, "theme": THEME,
                    "view": path, "kind": "page"}
            rec = (raw_page or {}).get("devices", {}).get(device) if isinstance(raw_page, dict) else None
            if not isinstance(rec, dict) or "error" in rec or not isinstance(rec.get("measure"), dict) or "error" in rec["measure"]:
                why = (rec or {}).get("error") or ((rec or {}).get("measure") or {}).get("error") or "not walked"
                screens.append({**base, "status": "error", "reason": str(why)})
                continue
            if rec.get("status") != 200:
                screens.append({**base, "status": "error", "reason": f"{path} answered {rec.get('status')}"})
                continue
            try:
                metrics = screen_metrics(path, device, raw_page, seo_doc, limits, delivery)  # type: ignore[arg-type]
            except (KeyError, TypeError) as exc:
                screens.append({**base, "status": "error", "reason": f"raw facts incomplete: {exc!r}"})
                continue
            screens.append({**base, "status": "ok", "metrics": metrics, "screenshot": rec.get("screenshot")})
    return screens


def _script_params(rubric: Rubric, tokens: Dict[str, str]) -> Dict[str, object]:
    params = resolve_params(rubric, tokens)
    generic = next((str(r.params["generic"]) for r in rubric.rules if r.metric == "page.vague_link_text_count" and r.params.get("generic")),
                   "click here|here|read more|more|learn more|link")
    label_min = float(re.sub(r"[^\d.]", "", tokens.get("type.label-min", "12")) or 12)
    return {"hit_min": params.get("hit_min", 44), "control": params.get("control", 48), "body_min": params.get("body_min", 16),
            "label_min": label_min, "generic": generic}


def _write(doc: Dict[str, object], run_dir: Path) -> Dict[str, object]:
    runtime_files.save_text(run_dir / "metrics.json", json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def measure_web(target: plan.Target, rubric: Rubric, tokens: Dict[str, str], devices: List[str], run_dir: Path,
                pages: Optional[List[str]] = None, all_pages: bool = False, python_override: Optional[str] = None,
                walk_timeout: float = WALK_TIMEOUT_S, perf: bool = True, spec_path: Path = WEB_SPEC,
                rubric_path: Path = WEB_RUBRIC) -> Dict[str, object]:
    """Measure a web page end to end; always writes `<run_dir>/metrics.json`, unmeasured runs included."""
    doc: Dict[str, object] = {
        "schema_version": measure.SCHEMA_VERSION, "rubric_version": rubric.version, "surface": SURFACE,
        "rubric_path": str(rubric_path), "spec_path": str(spec_path), "spec_user_scope": spec_user_scope(),
        "target": target.name, "root": str(target.root) if target.root else None, "base_url": None,
        "mode": "static-preview", "commit": capture.target_commit(target), "generated_at": capture.iso(capture.utc_now()),
        "run_dir": str(run_dir), "devices": devices, "pages": [], "review": target.review, "screens": [], "unmeasured": None,
    }
    interp = capture.resolve_interpreter(target, python_override)
    doc["interpreter"] = interp.get("python")
    if interp.get("status") != "ok":
        doc["unmeasured"] = {"reason": interp.get("status") or "PLAYWRIGHT_MISSING", "detail": interp.get("detail")}
        return _write(doc, run_dir)
    try:
        preview_plan = preview.resolve_preview(target.root, target.review)
    except preview.PreviewError as exc:
        doc["unmeasured"] = {"reason": exc.reason, "detail": exc.detail}
        return _write(doc, run_dir)
    doc["preview"] = {"source": preview_plan.source, "site_dir": str(preview_plan.site_dir) if preview_plan.site_dir else None}
    params = _script_params(rubric, tokens)
    runtime_files.save_text(run_dir / "params.json", json.dumps(params), encoding="utf-8")
    try:
        with preview.PreviewServer(str(interp["python"]), preview_plan, run_dir / "preview.log") as server:
            doc["base_url"] = server.base_url
            doc["preview"]["pid"] = server.pid  # type: ignore[index]
            fetch = seo.http_fetch(server.base_url)
            if all_pages:
                listed, _, problem = seo.sitemap_paths(fetch)
                pages = listed or ["/"]
                if problem:
                    doc["pages_note"] = problem
            pages = list(dict.fromkeys(pages or default_pages(target.root)))
            doc["pages"] = pages
            walk = _spawn_walk(str(interp["python"]), server.base_url, pages, devices, run_dir, walk_timeout, perf)
            doc["walk"] = walk
            try:
                seo_doc = seo.measure_site(fetch, pages, preview_plan.site_dir, tokens, declared_page_types(target.review))
            except Exception as exc:  # noqa: BLE001 - an unreadable site leaves the search facts unmeasured, not the run
                seo_doc = {"error": f"search facts: {type(exc).__name__}: {exc}"}
    except preview.PreviewError as exc:
        doc["unmeasured"] = {"reason": exc.reason, "detail": exc.detail}
        return _write(doc, run_dir)
    raw_path = run_dir / "raw.json"
    if not raw_path.is_file():
        doc["unmeasured"] = {"reason": "WALK_FAILED", "detail": walk.get("stderr_tail") or "no raw.json written"}
        return _write(doc, run_dir)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    headers = preview_plan.site_dir / "_headers" if preview_plan.site_dir else None
    delivery = checks.delivery_fails(headers.read_text(encoding="utf-8", errors="replace") if headers and headers.is_file() else None)
    doc["screens"] = build_screens(raw, pages, devices, seo_doc, checks.limits_from_spec(tokens), delivery)
    if "error" not in seo_doc:
        doc["seo_kinds"] = {p: v.get("kind") for p, v in seo_doc["pages"].items() if isinstance(v, dict)}  # type: ignore[union-attr]
    return _write(doc, run_dir)


def _spawn_walk(python: str, base_url: str, pages: List[str], devices: List[str], run_dir: Path,
                timeout: float, perf: bool) -> Dict[str, object]:
    argv = [python, str(PAGE_WALK), "--url", base_url, "--pages", ",".join(pages), "--devices", ",".join(devices),
            "--params", str(run_dir / "params.json"), "--out", str(run_dir)] + ([] if perf else ["--no-perf"])
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"returncode": None, "timed_out": True, "stderr_tail": f"walk exceeded {timeout:g}s"}
    runtime_files.save_text(run_dir / "walk.log", proc.stderr or "", encoding="utf-8")
    return {"returncode": proc.returncode, "timed_out": False, "stderr_tail": (proc.stderr or "")[-600:]}


def load_tokens(spec: Optional[Path]) -> Dict[str, str]:
    return parse_spec(Path(spec or WEB_SPEC).read_text(encoding="utf-8"))
