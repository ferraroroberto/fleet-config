"""Dialog and editor-modal contracts (design.md `modal`; fleet-config#307).

Native `<dialog>` vs a hand-rolled overlay, plus the five editor-modal checks
(rows / fieldset / header / footer / top-anchor) that all read the one `modals`
scan `_ContractsCtx` carries, so they stay one function rather than five that
each re-derive it.
"""
from __future__ import annotations

import re
from typing import List

from ..markup import (
    _BUTTON_RE,
    _FIELDSET_OPEN_RE,
    _FIELDSET_TAG_PAT,
    _FOOTER_CONTAINER_RE,
    _LABEL_CLASS_RE,
    _SAVE_AFFORDANCE_RE,
    _TAG_CLASS_RE,
    _loc_in_modal,
)
from ..selectors import _class_scope_status, _selector_hits
from ._ctx import _ContractsCtx, _evidence, _result


def _check_native_dialog(ctx: _ContractsCtx) -> List[dict]:
    # 7. native <dialog> vs hand-rolled overlay
    markup_all = ctx.markup_all
    if re.search(r"<dialog|\.showModal\(", markup_all):
        return [_result("native-dialog", "PASS", "native <dialog> / showModal() in use",
                         _evidence(markup_all, r"<dialog|\.showModal\("))]
    if re.search(r"class=[\"'][^\"']*(modal|overlay)", markup_all):
        return [_result("native-dialog", "WARN", "modal/overlay classes without native <dialog> — hand-rolled dialog suspected",
                         _evidence(markup_all, r"class=[\"'][^\"']*(modal|overlay)"))]
    return [_result("native-dialog", "NA", "no dialogs found")]


def _check_editor_modal_contract(ctx: _ContractsCtx) -> List[dict]:
    # 16-20. Editor-modal contract (design.md `modal` component;
    #     fleet-config#307) — a <dialog> containing real editable fields
    #     (<form> wrapper optional, fleet-config#342) is held to the
    #     header/rows/fieldset/footer/top-anchor contract. All five share
    #     the one `modals` scan, so they live in one function rather than
    #     re-deriving it per check.
    css_all = ctx.css_all
    modals = ctx.modals
    if not modals:
        return [_result(cid, "NA", "no editor-modal <dialog> found")
                for cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
                            "modal-footer", "modal-top-anchor")]

    results: List[dict] = []

    # classes carried by the <dialog> tags themselves and by elements inside
    # them — a rule scoped under any of these is dialog-scoped even when the
    # ancestor name has no "dialog" in it (`.detail-card .row`, #342).
    dialog_classes_set: set = set()
    for modal in modals:
        dialog_classes_set |= modal["classes"]
        for cm in _TAG_CLASS_RE.finditer(modal["inner"]):
            dialog_classes_set.update(cm.group(1).split())
    dialog_classes = frozenset(dialog_classes_set)

    # 16. unstyled dialog rows — a dialog row (label) whose classes are only
    #     ever styled under some other, unrelated scope. Judged per label,
    #     not per class: one properly styled class makes the row styled — an
    #     unstyled *modifier* riding a styled base class is fine (#342).
    unstyled: List[str] = []
    for modal in modals:
        for lm in _LABEL_CLASS_RE.finditer(modal["inner"]):
            classes = lm.group(1).split()
            statuses = [_class_scope_status(css_all, cls, dialog_classes)
                        for cls in classes]
            if any("global" in s or "dialog" in s for s in statuses):
                continue
            loc = _loc_in_modal(modal, lm.start())
            why = ("styled only outside dialogs" if any(statuses)
                   else "never styled")
            unstyled.append(f"{loc} label.{'.'.join(classes)} ({why})")
    if unstyled:
        results.append(_result("modal-unstyled-rows", "FAIL",
            f"{len(unstyled)} dialog row class(es) with no dialog-scoped "
            "styling (design.md modal contract): " + "; ".join(unstyled[:6])))
    else:
        results.append(_result("modal-unstyled-rows", "PASS",
            "every dialog row class is styled globally or in a dialog-scoped rule"))

    # 17. raw <fieldset> — a fieldset/legend with no authored CSS at all,
    #     rendering as a raw browser legend box.
    raw_fieldsets: List[str] = []
    for modal in modals:
        for fm in _FIELDSET_OPEN_RE.finditer(modal["inner"]):
            cls_m = _TAG_CLASS_RE.search(fm.group(1))
            classes = cls_m.group(1).split() if cls_m else []
            if classes:
                authored = any(_class_scope_status(css_all, c) for c in classes)
            else:
                authored = bool(_selector_hits(css_all, _FIELDSET_TAG_PAT))
            if not authored:
                raw_fieldsets.append(_loc_in_modal(modal, fm.start()))
    if raw_fieldsets:
        results.append(_result("modal-raw-fieldset", "FAIL",
            f"{len(raw_fieldsets)} <fieldset> with no authored CSS — raw "
            "browser legend box (design.md modal wants titled plain "
            "sections, never a fieldset): " + ", ".join(raw_fieldsets[:6])))
    else:
        results.append(_result("modal-raw-fieldset", "PASS", "no unstyled <fieldset> found in editor modals"))

    # 18. header contract — a title needs a square × close button; a
    #     footer "Cancel" button in its place is the anti-pattern.
    header_bad: List[str] = []
    header_checked = 0
    for modal in modals:
        if not re.search(r"<h[1-6]\b", modal["inner"], re.I):
            continue
        header_checked += 1
        has_close = False
        has_cancel = False
        for bm2 in _BUTTON_RE.finditer(modal["inner"]):
            battrs, btext = bm2.group(1), bm2.group(2)
            if (re.search(r'aria-label=["\']close["\']', battrs, re.I)
                    or re.search(r'class=["\'][^"\']*\bclose\b[^"\']*["\']', battrs, re.I)):
                has_close = True
            text_clean = re.sub(r"<[^>]+>", "", btext).strip()
            if re.fullmatch(r"cancel", text_clean, re.I):
                has_cancel = True
        if not has_close or has_cancel:
            reasons = []
            if not has_close:
                reasons.append("no square x close button")
            if has_cancel:
                reasons.append("footer Cancel button in its place")
            header_bad.append(f"{modal['file']}:{modal['line']} " + " + ".join(reasons))
    if header_checked == 0:
        results.append(_result("modal-header", "NA", "no titled editor-modal <dialog> found"))
    elif header_bad:
        results.append(_result("modal-header", "FAIL",
            "editor-modal header contract violated (design.md modal wants "
            "a heading-lg title + square x close, never a footer Cancel): "
            + "; ".join(header_bad[:6])))
    else:
        results.append(_result("modal-header", "PASS", "editor-modal header(s) carry a square x close, no footer Cancel"))

    # 19. footer contract — exactly one always-visible action, styled as
    #     the full-width solid-accent primary. Applies only to *staged*
    #     editors (a Save/submit persistence boundary exists): a live-control
    #     dialog with fields but no Save (a camera PTZ surface, a filter
    #     panel) has action rails, not a persistence footer (#342).
    footer_bad: List[str] = []
    footer_checked = 0
    for modal in modals:
        if not _SAVE_AFFORDANCE_RE.search(modal["inner"]):
            continue
        footer = None
        for fm2 in _FOOTER_CONTAINER_RE.finditer(modal["inner"]):
            fattrs, fbody = fm2.group(2), fm2.group(3)
            fcls_m = _TAG_CLASS_RE.search(fattrs)
            fclasses = fcls_m.group(1).split() if fcls_m else []
            if not any(re.search(r"actions|footer", c, re.I) for c in fclasses):
                continue
            # the footer is terminal — interactive content after the
            # container means this is a mid-body action rail, not the
            # persistence footer (#342, the camera live-view rail).
            if re.search(r"<(button|input|select|textarea|label)\b",
                         modal["inner"][fm2.end():], re.I):
                continue
            footer = (fclasses, fbody)
            break
        if footer is None:
            continue
        footer_checked += 1
        fclasses, fbody = footer
        visible = [bm2 for bm2 in _BUTTON_RE.finditer(fbody)
                   if not re.search(r"(^|\s)hidden(\s|=|$)", bm2.group(1), re.I)]
        if len(visible) > 1:
            footer_bad.append(f"{modal['file']}:{modal['line']} "
                               f"{len(visible)} always-visible footer actions")
            continue
        if len(visible) == 0:
            continue
        btn_cls_m = _TAG_CLASS_RE.search(visible[0].group(1))
        btn_classes = btn_cls_m.group(1).split() if btn_cls_m else []
        is_accent = False
        is_full_width = False
        for c in btn_classes:
            for _, _anc, body in _selector_hits(css_all, re.compile(r"\." + re.escape(c) + r"\b")):
                if re.search(r"background(-color)?:\s*var\(--accent\)", body):
                    is_accent = True
                if re.search(r"width:\s*100%|flex:\s*1\b|align-self:\s*stretch", body):
                    is_full_width = True
        for c in fclasses:
            for _, _anc, body in _selector_hits(css_all, re.compile(r"\." + re.escape(c) + r"\b")):
                if (re.search(r"flex-direction:\s*column", body)
                        and re.search(r"align-items:\s*stretch", body)):
                    is_full_width = True
        if not (is_accent and is_full_width):
            missing = []
            if not is_accent:
                missing.append("not the solid-accent primary recipe")
            if not is_full_width:
                missing.append("not full-width")
            footer_bad.append(f"{modal['file']}:{modal['line']} primary " + " + ".join(missing))
    if footer_checked == 0:
        results.append(_result("modal-footer", "NA",
            "no staged editor modal with a Save affordance + locatable footer/actions container"))
    elif footer_bad:
        results.append(_result("modal-footer", "FAIL",
            "editor-modal footer contract violated (design.md modal wants "
            "exactly one full-width solid-accent primary): "
            + "; ".join(footer_bad[:6])))
    else:
        results.append(_result("modal-footer", "PASS",
            "editor-modal footer(s) carry exactly one full-width solid-accent primary"))

    # 20. top-anchoring — a tall form must not jump vertically as
    #     conditional rows toggle; the dialog itself scrolls internally.
    top_anchor_bad: List[str] = []
    for modal in modals:
        ok = False
        for c in modal["classes"]:
            for _, _anc, body in _selector_hits(css_all, re.compile(r"\." + re.escape(c) + r"\b")):
                if re.search(r"max-height", body) and re.search(r"overflow(-y)?:\s*auto", body):
                    ok = True
        if not ok:
            for _, _anc, body in _selector_hits(css_all, re.compile(r"^dialog(?:[.:#\[]|$)", re.I)):
                if re.search(r"max-height", body) and re.search(r"overflow(-y)?:\s*auto", body):
                    ok = True
        if not ok:
            top_anchor_bad.append(f"{modal['file']}:{modal['line']}")
    if top_anchor_bad:
        results.append(_result("modal-top-anchor", "FAIL",
            "editor-modal(s) with no max-height + internal scroll — a tall "
            "form jumps as conditional rows toggle (design.md modal: "
            "top-anchored on mobile): " + ", ".join(top_anchor_bad[:6])))
    else:
        results.append(_result("modal-top-anchor", "PASS",
            "editor-modal(s) are top-anchored with internal scroll"))

    return results
