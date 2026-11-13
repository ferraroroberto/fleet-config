"""Navigation shell + viewport contracts.

The nav contract (hide-under-dialog, viewport anchor, safe-area inset, the
installed-app 100lvh bar anchor, and the load-bearing `nav.tabs`-is-a-`<body>`-
sibling rule), the installable-PWA viewport zoom lock and `viewport-fit=auto`,
and the `black-translucent` status-bar style its top glass is written for.
"""
from __future__ import annotations

import re
from typing import List

from ..css import strip_comments
from ..files import read_text, rel
from ..markup import (
    nav_nested_in_app,
    standalone_bar_anchor_present,
    standalone_shell_present,
    status_bar_style,
)
from ._ctx import _ContractsCtx, _result


def _check_nav_contract(ctx: _ContractsCtx) -> List[dict]:
    # 8. nav contract signals — architecture decides, not provenance. A vendored
    #    copy still needs the installed-app bar anchor, and a hand-carried nav
    #    with the full architecture passes on merit. Provenance is the
    #    `vendored` lens's job.
    css_all = ctx.css_all
    signals = {
        "hide-under-dialog (body:has(dialog[open]))": r"body:has\(dialog\[open\]\)",
        "viewport anchor (100dvh)": r"100dvh",
        "safe-area inset": r"safe-area-inset-bottom",
    }
    missing = [name for name, pat in signals.items()
               if not re.search(pat, css_all)]
    anchor_ok = standalone_bar_anchor_present(css_all)
    old_shell = standalone_shell_present(css_all)
    vendored_root = ctx.vendored_root
    vendored_nav = vendored_root is not None and (vendored_root / "nav" / "nav-tabs.css").exists()
    provenance = "vendored" if vendored_nav else "hand-carried"
    # nav-nesting: nav.tabs must be a <body> sibling of main.app, never a
    # descendant (_vendored/nav/README.md) — a structural
    # violation the CSS signals above can't see, so it FAILs on its own
    # regardless of everything else passing.
    if nav_nested_in_app(ctx.index_files):
        return [_result("nav-contract", "FAIL",
                         "nav-nesting: nested-inside-app — <nav class=\"tabs\"> must be a "
                         "direct <body> sibling of <main class=\"app\">, never nested "
                         "inside it (_vendored/nav/README.md; app-launcher#369)")]
    if old_shell:
        return [_result("nav-contract", "WARN",
                         "superseded standalone shell: a fixed-inset .app scroller under "
                         "(display-mode: standalone) — the nav contract keeps the window as "
                         "the scroller and anchors the bar from the top with 100lvh; "
                         "re-vendor _vendored/nav/ and drop the shell")]
    if not missing and anchor_ok:
        return [_result("nav-contract", "PASS",
                         f"nav-nesting: sibling (PASS); nav contract signals + standalone "
                         f"100lvh bar anchor all present ({provenance})")]
    if not anchor_ok:
        missing.append("standalone 100lvh bar anchor (.tabs, bottom: auto)")
    if len(missing) < len(signals) + 1:
        return [_result("nav-contract", "WARN", "nav contract partially present — missing: " + ", ".join(missing))]
    return [_result("nav-contract", "FAIL", "no nav-contract signals — adopt the vendored nav from project-scaffolding")]


def _check_status_bar_style(ctx: _ContractsCtx) -> List[dict]:
    # 11. page head — the vendored nav's top glass is written for the
    #     `black-translucent` status-bar style (design.md nav contract). Any
    #     other value leaves the glass without the viewport it was built for.
    index_files = ctx.index_files
    if not index_files:
        return [_result("status-bar-style", "NA", "no index.html found")]
    default: List[str] = []
    other: List[str] = []
    for p in index_files:
        style = status_bar_style(strip_comments(read_text(p), "html"))
        if style == "black-translucent":
            continue
        (default if style == "default" else other).append(rel(ctx.root, p))
    if default:
        return [_result("status-bar-style", "WARN",
                         "apple-mobile-web-app-status-bar-style is default — the vendored "
                         "nav's top glass is written for \"black-translucent\" "
                         "(design.md nav contract): " + ", ".join(default))]
    if other:
        return [_result("status-bar-style", "WARN",
                         "apple-mobile-web-app-status-bar-style is missing or not "
                         "\"black-translucent\" (design.md nav contract): " + ", ".join(other))]
    return [_result("status-bar-style", "PASS",
                     f"status-bar style \"black-translucent\" on {len(index_files)} index.html")]


def _check_viewport_lock(ctx: _ContractsCtx) -> List[dict]:
    # 10. viewport zoom lock — installable PWAs pin the scale (design.md Layout):
    #     user-scalable=no + maximum-scale=1, plus the `viewport-fit=auto` the
    #     vendored nav's standalone head ships (`cover` makes the viewport run
    #     under the status bar, which the nav's top glass is not written for).
    index_files = ctx.index_files
    if not index_files:
        return [_result("viewport-lock", "NA", "no index.html found")]
    broken: List[str] = []
    wrong_fit: List[str] = []
    for p in index_files:
        text = strip_comments(read_text(p), "html")
        m = re.search(r"<meta[^>]+name=[\"']viewport[\"'][^>]*>", text, re.I)
        tag = m.group(0) if m else ""
        if not m or "user-scalable=no" not in tag or "maximum-scale=1" not in tag:
            broken.append(rel(ctx.root, p))
        elif "viewport-fit=auto" not in tag:
            wrong_fit.append(rel(ctx.root, p))
    if broken:
        return [_result("viewport-lock", "FAIL",
                         "viewport meta lacks the zoom lock (user-scalable=no + "
                         "maximum-scale=1 — design.md Layout, fleet-config#296): "
                         + ", ".join(broken))]
    if wrong_fit:
        return [_result("viewport-lock", "WARN",
                         "zoom lock present but viewport-fit=auto missing — the vendored "
                         "nav ships auto, and cover is the old value (design.md nav "
                         "contract): " + ", ".join(wrong_fit))]
    return [_result("viewport-lock", "PASS",
                     f"zoom lock (user-scalable=no, maximum-scale=1, viewport-fit=auto) on {len(index_files)} index.html")]
