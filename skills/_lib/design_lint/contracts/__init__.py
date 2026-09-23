"""The `contracts` lens: greppable design.md v2 component-contract checks.

`contracts()` is a thin orchestrator — it builds the shared `_ContractsCtx`
once and runs `_CONTRACT_CHECKS` in numbered order. Each check lives in the
per-concern module its subject belongs to (a11y, controls, modal, nav, icons,
charts, states, typography), so adding a design.md rule means editing one small file and
adding one row to the tuple below, not growing a single 1200-line section.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from ..css import strip_comments
from ..files import find_vendored_root, is_third_party, read_text, rel
from ..markup import _editor_modals
from ._ctx import _ContractsCtx
from .a11y import (
    _check_desktop_measure,
    _check_focus_visible_ring,
    _check_hit_target,
    _check_reduced_motion,
    _check_rendered_leg,
    _check_spec_contrast,
)
from .charts import _check_chart_noncolor_cue, _check_chart_tick_budget
from .controls import (
    _check_button_tiers,
    _check_chevron_placement,
    _check_disclosure_box,
    _check_no_native_checkbox,
    _check_row_height_scale,
    _check_switch_on_green,
    _check_text_size,
    _check_theme_toggle,
)
from .icons import _check_app_icon_family, _check_glyph_icons, _check_icon_set, _check_icon_sizes
from .modal import _check_editor_modal_contract, _check_native_dialog
from .nav import _check_nav_contract, _check_viewport_lock
from .states import _check_async_lifecycle
from .typography import _check_break_all, _check_form_font_inherit, _check_uppercase_role


_CONTRACT_CHECKS: Tuple[Callable[["_ContractsCtx"], List[dict]], ...] = (
    _check_focus_visible_ring,
    _check_reduced_motion,
    _check_desktop_measure,
    _check_switch_on_green,
    _check_no_native_checkbox,
    _check_disclosure_box,
    _check_native_dialog,
    _check_nav_contract,
    _check_icon_sizes,
    _check_viewport_lock,
    _check_button_tiers,
    _check_theme_toggle,
    _check_text_size,
    _check_icon_set,
    _check_glyph_icons,
    _check_app_icon_family,
    _check_chevron_placement,
    _check_row_height_scale,
    _check_editor_modal_contract,
    _check_hit_target,
    _check_chart_tick_budget,
    _check_chart_noncolor_cue,
    _check_async_lifecycle,
    _check_form_font_inherit,
    _check_uppercase_role,
    _check_break_all,
    _check_spec_contrast,
    _check_rendered_leg,
)


def contracts(
    root: Path,
    css_files: List[Path],
    html_files: List[Path],
    js_files: List[Path],
    spec_light: Dict[str, str],
    spec_dark: Optional[Dict[str, str]] = None,
) -> List[dict]:
    """Greppable design.md v2 component-contract checks (focus ring, reduced
    motion, desktop measure, switch on-color, native checkboxes, disclosure
    box, native <dialog>, nav rules, icon-size strays, ...). Thin orchestrator
    over `_CONTRACT_CHECKS`: builds the shared `_ContractsCtx` once, then runs
    each check in numbered order and concatenates its `checks` entries."""
    def _css_blob(paths: List[Path]) -> str:
        return "\n".join(f"/*FILE {rel(root, p)}*/\n" + strip_comments(read_text(p), "css")
                         for p in paths)

    css_all = _css_blob(css_files)
    # app-authored CSS only. A bundled third-party library's internals are not
    # the adopting app's design choice and can't be fixed there anyway — the
    # reason `find_emoji_sites` already skips them (#416). Leaflet's popup
    # close button is not home-automation's button (fleet-config#940).
    # `files.is_third_party` states which blob a new contract should read.
    css_own = _css_blob([p for p in css_files if not is_third_party(p)])

    def _markup_blob(paths: List[Path]) -> str:
        return "\n".join(
            f"/*FILE {rel(root, p)}*/\n"
            + strip_comments(read_text(p), "html" if p.suffix == ".html" else "js")
            for p in paths)

    markup_files = html_files + js_files
    markup_all = _markup_blob(markup_files)
    # app-authored markup only, same rule as `css_own` — Leaflet's own
    # layers-control checkbox is not home-automation's checkbox (#843).
    markup_own = _markup_blob([p for p in markup_files if not is_third_party(p)])
    index_files = [p for p in html_files if p.name == "index.html"]
    ctx = _ContractsCtx(
        root=root,
        css_all=css_all,
        css_own=css_own,
        markup_all=markup_all,
        markup_own=markup_own,
        spec_light=spec_light,
        spec_dark=spec_dark,
        html_files=html_files,
        js_files=js_files,
        index_files=index_files,
        vendored_root=find_vendored_root(root),
        modals=_editor_modals(root, html_files),
    )
    checks: List[dict] = []
    for check_fn in _CONTRACT_CHECKS:
        checks.extend(check_fn(ctx))
    return checks
