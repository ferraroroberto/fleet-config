"""The pass rules of the web-page review: raw walk facts in, rubric metrics out (design.web.md section 14).

Pure functions, stdlib only, unit-tested against known-bad fixtures without a
browser. Each takes what `page_walk.py` recorded and returns the value the
rubric metric reads: a list of findings (its length is the count, its items
the sample), a number, or `None` when the fact could not be established —
`None` is reported as unmeasured, never as a pass.

The rules, as design.web.md states them:

  phone reading order   per row (or section), a picture read before a text block on a phone fails,
                        unless the desktop shows the picture first: above the text, or beside it on the left
  text squeezed         beside a picture, a text column gets at least a third of its row
  image roles           images of one role (feature, tile, thumb, one card set) render within 2 px of each other
  portraits             one size and radius in a list, top level with the name within 4 px
  carousel cards        3 whole cards from 1024 px and 2 from 640 px when it scrolls, never a partial card;
                        on a phone 1 whole card plus the next one's edge
  carousel step         one click moves one card plus the gap, the arrows sit centred under the row (2 px)
  sticky media          beside the text, no blank stretch over 200 px (scanned through a 1440x900 viewport)
  menu hit test         the centre of each open phone-menu link, through elementFromPoint, is that link
  footer contrast       body text on the band 7:1, label heads 4.5:1
  first-load weight     transferred bytes and requests within the page type's budget
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

ROLE_TOLERANCE_PX = 2.0
PORTRAIT_TOP_TOLERANCE_PX = 4.0
EDGE_TOLERANCE_PX = 1.0
DESKTOP_MIN_PX = 1024
TWO_UP_MIN_PX = 640
WOFF2_RE = re.compile(r"\.woff2(\?|$|[\"')])", re.I)
GOOD_FONT_DISPLAY = ("swap", "optional", "fallback")
YEAR_S = 31536000  # "long-lived" caching for hashed assets: a year, or `immutable`


def _size(text: object, unit_bytes: Dict[str, float]) -> Optional[float]:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*$", str(text))
    if not m:
        return None
    return float(m.group(1)) * unit_bytes.get(m.group(2).upper(), 1.0)


def limits_from_spec(tokens: Dict[str, str]) -> Dict[str, object]:
    """The budgets and font limits from design.web.md's front matter, as numbers (bytes, counts)."""
    kb = {"": 1.0, "B": 1.0, "KB": 1024.0, "MB": 1024.0 * 1024.0}
    budgets: Dict[str, Dict[str, float]] = {}
    for key, value in tokens.items():
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "budgets":
            n = _size(value, kb)
            if n is not None:
                budgets.setdefault(parts[1], {})[parts[2]] = n
    return {
        "budgets": budgets,
        "font_files_max": _size(tokens.get("performance.font-files-max", "4"), kb),
        "font_bytes_max": _size(tokens.get("performance.font-bytes-max", "150KB"), kb),
        "html_max": _size(tokens.get("performance.html-max", "50KB"), kb),
        "js_first_party_max": _size(tokens.get("performance.js-first-party-max", "50KB"), kb),
    }


def phone_order_violations(phone: List[dict], desktop: Optional[List[dict]]) -> Optional[List[str]]:
    """One finding per row whose phone order reads a picture before a text block the desktop does not show after it."""
    if desktop is None:
        return None
    at_desktop = {b["key"]: b for b in desktop}
    groups: Dict[str, List[dict]] = {}
    for b in phone:
        groups.setdefault(b["group"], []).append(b)
    out: List[str] = []
    for group, blocks in groups.items():
        blocks = sorted(blocks, key=lambda b: (b["top"], b.get("left", 0)))
        found = None
        for i, pic in enumerate(blocks):
            if pic["kind"] != "picture":
                continue
            for text in blocks[i + 1:]:
                if text["kind"] != "text" or _desktop_shows_first(at_desktop.get(pic["key"]), at_desktop.get(text["key"])):
                    continue
                found = f"section {group.split(':')[0]}: {pic['text']} before {text['text']}"
                break
            if found:
                break
        if found:
            out.append(found)
    return out


def _desktop_shows_first(pic: Optional[dict], text: Optional[dict]) -> bool:
    """The desktop reads the picture first: entirely above the text, or beside it on its left."""
    if not pic or not text:
        return False
    if text["top"] >= pic["bottom"] - 4:
        return True
    beside = text["top"] < pic["bottom"] and pic["top"] < text["bottom"]
    return beside and pic.get("right", 0) <= text.get("left", 0) + EDGE_TOLERANCE_PX


def text_squeezed(media_rows: List[dict]) -> List[str]:
    return [f"{r['text']} gets {r['text_w']}px of a {r['row_w']}px row" for r in media_rows
            if r.get("row_w") and r["text_w"] < r["row_w"] / 3.0]


def image_role_spread(role_images: List[dict]) -> Tuple[float, List[str]]:
    """The widest spread within one role, and the role(s) that set it."""
    widths: Dict[str, List[float]] = {}
    for img in role_images:
        widths.setdefault(str(img["role"]), []).append(float(img["w"]))
    worst, roles = 0.0, []
    for role, ws in widths.items():
        spread = max(ws) - min(ws) if len(ws) > 1 else 0.0
        if spread > worst:
            worst, roles = spread, [role]
    return worst, roles


def portrait_misaligned(portraits: List[dict]) -> List[str]:
    if not portraits:
        return []
    out: List[str] = []
    ref = portraits[0]
    for p in portraits:
        why = []
        if abs(p["w"] - ref["w"]) > ROLE_TOLERANCE_PX or abs(p["h"] - ref["h"]) > ROLE_TOLERANCE_PX:
            why.append(f"{p['w']}x{p['h']} vs {ref['w']}x{ref['h']}")
        if p.get("radius") != ref.get("radius"):
            why.append(f"radius {p.get('radius')} vs {ref.get('radius')}")
        if p.get("name_top") is not None and abs(p["top"] - p["name_top"]) > PORTRAIT_TOP_TOLERANCE_PX:
            why.append(f"top {p['top'] - p['name_top']:+}px from the name")
        if why:
            out.append(f"{p['src']}: {', '.join(why)}")
    return out


def _whole_and_partial(c: dict) -> Tuple[int, int]:
    whole = partial = 0
    for card in c["cards"]:
        inside = card["left"] >= c["left"] - EDGE_TOLERANCE_PX and card["right"] <= c["right"] + EDGE_TOLERANCE_PX
        visible = card["right"] > c["left"] + EDGE_TOLERANCE_PX and card["left"] < c["right"] - EDGE_TOLERANCE_PX
        if inside:
            whole += 1
        elif visible:
            partial += 1
    return whole, partial


def carousel_partial(carousels: List[dict], viewport_w: int) -> List[str]:
    """The carousels that break the whole-card rule at this viewport width."""
    out: List[str] = []
    for c in carousels:
        cards = len(c["cards"])
        if cards == 0:
            continue
        whole, partial = _whole_and_partial(c)
        if viewport_w < TWO_UP_MIN_PX:
            want_edge = cards > 1
            if whole != 1 or (want_edge and partial < 1) or partial > 1:
                out.append(f"carousel {c['index']}: {whole} whole + {partial} partial at {viewport_w}px (want 1 whole + the next card's edge)")
            continue
        per_row = 3 if viewport_w >= DESKTOP_MIN_PX else 2
        # Cards per visible row: a non-scrolling grid of testimonials can stack rows, so count the first row only.
        first_top = min(card["top"] for card in c["cards"])
        row = {**c, "cards": [card for card in c["cards"] if abs(card["top"] - first_top) <= EDGE_TOLERANCE_PX]}
        whole, partial = _whole_and_partial(row)
        if partial or (c.get("scrolls") and whole != per_row):
            out.append(f"carousel {c['index']}: {whole} whole + {partial} partial at {viewport_w}px (want {per_row} whole, 0 partial)")
    return out


def carousel_step_error(carousels: List[dict], steps: List[dict]) -> Tuple[float, List[str]]:
    """The largest error in px: one click against one card plus the gap, and the arrows against the row's centre."""
    worst, items = 0.0, []
    for c in carousels:
        if c.get("scrolls") and c.get("nav_centre") is not None:
            off = abs(float(c["nav_centre"]) - (float(c["left"]) + float(c["right"])) / 2.0)
            if off > worst:
                worst = off
            items.append(f"carousel {c['index']}: arrows {off:.1f}px off centre")
    for s in steps:
        if "error" in s:
            items.append(f"carousel {s['index']}: {s['error']}")
            continue
        err = abs(float(s["step"]) - float(s["expected"]))
        worst = max(worst, err)
        items.append(f"carousel {s['index']}: moved {s['step']:.0f}px for a {s['expected']:.0f}px card step")
    return round(worst, 1), items


def footer_contrast_fails(footer_runs: List[dict]) -> List[str]:
    return [f"{r['text']} {r['ratio']}:1 (floor {r['floor']})" for r in footer_runs if r["ratio"] < r["floor"]]


def menu_misses(menu: dict) -> Tuple[Optional[List[str]], Optional[str]]:
    """`(misses, None)`, or `(None, reason)` when the hit test could not run."""
    if not isinstance(menu, dict):
        return None, "menu check did not run"
    if "error" in menu:
        return None, f"menu check failed: {menu['error']}"
    if not menu.get("nav"):
        return None, "no site nav found"
    links = menu.get("links") or []
    if not links:
        return None, "the menu holds no links"
    return [f"{link['href']} is not the element at its own centre" for link in links if not link["hit"]], None


def budget_over(perf: dict, page_type: str, limits: Dict[str, object]) -> Optional[List[str]]:
    budgets = limits.get("budgets") or {}
    budget = budgets.get(page_type) if isinstance(budgets, dict) else None
    if not budget:
        return None
    out = []
    if "bytes" in budget and perf["bytes"] > budget["bytes"]:
        out.append(f"{page_type}: {perf['bytes'] / 1024:.0f} KB > {budget['bytes'] / 1024:.0f} KB")
    if "requests" in budget and perf["requests"] > budget["requests"]:
        out.append(f"{page_type}: {perf['requests']} requests > {budget['requests']:.0f}")
    html_max = limits.get("html_max")
    if isinstance(html_max, float) and perf.get("html_bytes", 0) > html_max:
        out.append(f"HTML {perf['html_bytes'] / 1024:.0f} KB > {html_max / 1024:.0f} KB")
    js_max = limits.get("js_first_party_max")
    if isinstance(js_max, float) and perf.get("first_party_js_bytes", 0) > js_max:
        out.append(f"first-party JS {perf['first_party_js_bytes'] / 1024:.0f} KB > {js_max / 1024:.0f} KB")
    return out


def font_fail(perf: dict, limits: Dict[str, object]) -> List[str]:
    out = []
    files = perf.get("font_files") or []
    files_max, bytes_max = limits.get("font_files_max"), limits.get("font_bytes_max")
    if isinstance(files_max, float) and len(files) > files_max:
        out.append(f"{len(files)} font files load (max {files_max:.0f})")
    total = sum(f["bytes"] for f in files)
    if isinstance(bytes_max, float) and total > bytes_max:
        out.append(f"{total / 1024:.0f} KB of fonts (max {bytes_max / 1024:.0f} KB)")
    for f in files:
        if not WOFF2_RE.search(f["url"]):
            out.append(f"{f['url']} is not WOFF2")
    for face in perf.get("font_faces") or []:
        if face.get("display") not in GOOD_FONT_DISPLAY:
            out.append(f"@font-face {face.get('family')} has font-display {face.get('display')}")
    return sorted(set(out), key=out.index)


def delivery_fails(headers_text: Optional[str]) -> List[str]:
    """What `_headers` leaves undone: long-lived caching for static assets (Cloudflare Pages compresses text itself)."""
    if headers_text is None:
        return ["no _headers file: static assets get the host's default max-age=0"]
    def long(line: str) -> bool:
        m = re.search(r"max-age=(\d+)", line, re.I)
        return "immutable" in line.lower() or (m is not None and int(m.group(1)) >= YEAR_S)

    long_lived = any(long(line) for line in headers_text.splitlines() if line.strip().lower().startswith("cache-control:"))
    if not long_lived:
        return ["_headers sets no long-lived Cache-Control (immutable or max-age of a year) for static assets"]
    return []
