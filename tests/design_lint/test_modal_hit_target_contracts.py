"""Unit tests for design_lint's editor-modal, hit-target, chart and async-lifecycle contracts (fleet-config#307, #342).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_lint/test_modal_hit_target_contracts.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import re

from _suite import _h, check  # noqa: E402
from design_lint_support import (  # noqa: E402
    GOOD_CSS, PRE_MODAL_CSS, PRE_MODAL_HTML,
    run_contracts,
)


# ---- editor-modal contract (design.md `modal` component, fleet-config#307) ----
# Fixtures mirror the real app-launcher#70 before/after (job-editor dialog):
# pre-fix `.stacked` styled only under `.settings-card`, a raw unstyled
# `.job-chain-fieldset`, a footer Cancel button instead of a header close,
# two always-visible footer actions, and no max-height/scroll; post-fix adopts
# the dialog-scoped stacked rows, drops the fieldset for a `.dialog-section`,
# moves Cancel into a header `.dialog-close`, collapses the footer to one
# full-width solid-accent primary, and top-anchors with an internal scroll.

pre_modal = run_contracts(PRE_MODAL_CSS, PRE_MODAL_HTML)
check(pre_modal["modal-unstyled-rows"]["status"] == "FAIL",
      "label.stacked styled only under .settings-card -> FAIL (the #70 root cause)")
check(pre_modal["modal-raw-fieldset"]["status"] == "FAIL",
      "raw .job-chain-fieldset with zero authored CSS -> FAIL")
check(pre_modal["modal-header"]["status"] == "FAIL",
      "no header close button + footer Cancel -> FAIL")
check(pre_modal["modal-footer"]["status"] == "FAIL",
      "two always-visible footer actions (Cancel + Add and verify) -> FAIL")
check(pre_modal["modal-top-anchor"]["status"] == "FAIL",
      "no max-height/overflow on .rename-dialog -> FAIL")
# every modal FAIL names its first offending dialog in `evidence`, so a
# [[design.accepted]] entry's `target` can match it (fleet-config#953)
for _cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
             "modal-footer", "modal-top-anchor"):
    check(re.fullmatch(r"i\.html:\d+", pre_modal[_cid]["evidence"] or "") is not None,
          f"{_cid}: FAIL carries a file:line evidence (#953)")

POST_MODAL_HTML = """
<dialog id="jobDialog" class="rename-dialog">
  <form id="jobForm">
    <div class="dialog-head">
      <h2 id="jobDialogTitle">Add job</h2>
      <button type="button" id="jobCancel" class="dialog-close" aria-label="Close">
        <svg class="icon"><use href="#i-x"></use></svg>
      </button>
    </div>
    <label class="stacked"><span>Name</span><input type="text" required></label>
    <section class="dialog-section"><h3 class="dialog-section-title">Run on success</h3></section>
    <div class="dialog-actions dialog-actions--stacked">
      <button type="button" id="jobSaveAnyway" class="big-btn warn" hidden>Save anyway</button>
      <button type="submit" id="jobSaveBtn" class="big-btn">Add and verify</button>
    </div>
  </form>
</dialog>
"""
POST_MODAL_CSS = """
.rename-dialog { margin-top: 16px; max-height: calc(100dvh - 32px); overflow-y: auto; }
.rename-dialog .stacked {
  display: flex; flex-direction: column; padding: 12px 0;
  border-top: 1px solid var(--line-muted);
}
.dialog-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
.dialog-actions--stacked { flex-direction: column; align-items: stretch; padding-top: 12px; }
.rename-dialog .dialog-actions .big-btn:not(.warn) {
  background: var(--accent); color: var(--accent-fg); min-height: 48px;
}
"""
post_modal = run_contracts(POST_MODAL_CSS, POST_MODAL_HTML)
check(post_modal["modal-unstyled-rows"]["status"] == "PASS",
      "dialog-scoped .rename-dialog .stacked rule -> PASS")
check(post_modal["modal-raw-fieldset"]["status"] == "PASS",
      "no <fieldset> left (replaced by .dialog-section) -> PASS")
check(post_modal["modal-header"]["status"] == "PASS",
      "header .dialog-close (aria-label=Close), no footer Cancel -> PASS")
check(post_modal["modal-footer"]["status"] == "PASS",
      "one always-visible full-width solid-accent primary -> PASS")
# fleet-config#994: #963 moved the solid primary to `accent-fill`
# (project-scaffolding#274); the vendored button-primary reads var(--accent-fill).
fill_modal = run_contracts(
    POST_MODAL_CSS.replace("background: var(--accent);", "background: var(--accent-fill);"),
    POST_MODAL_HTML)
check(fill_modal["modal-footer"]["status"] == "PASS",
      "an accent-fill solid primary is the solid-accent recipe -> PASS (#994)")
soft_modal = run_contracts(
    POST_MODAL_CSS.replace("background: var(--accent);", "background: var(--accent-soft);"),
    POST_MODAL_HTML)
check(soft_modal["modal-footer"]["status"] == "FAIL",
      "an accent-soft tint is still not the solid primary -> FAIL (#994)")
check(post_modal["modal-top-anchor"]["status"] == "PASS",
      "max-height + overflow-y: auto on .rename-dialog -> PASS")

# a dialog with no editable FIELDS (an alert/confirm) is not an "editor
# modal" -> NA across the board. The boundary is fields, not <form> —
# fleet-config#342 dropped the <form> requirement (see the form-less
# editor fixture below), so this fixture stays field-less on purpose.
NON_FORM_DIALOG = '<dialog class="scan-dialog"><p>Are you sure?</p><button type="button">OK</button></dialog>'
non_form = run_contracts(GOOD_CSS, NON_FORM_DIALOG)
for _cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
             "modal-footer", "modal-top-anchor"):
    check(non_form[_cid]["status"] == "NA", f"{_cid}: field-less dialog -> NA, not a finding")

# no <dialog> at all -> NA across the board
no_dialog = run_contracts(GOOD_CSS)
for _cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
             "modal-footer", "modal-top-anchor"):
    check(no_dialog[_cid]["status"] == "NA", f"{_cid}: no dialog in the app -> NA")

# ---- #342: form-less JS-managed editor dialogs are real editors ----
# The exact home-automation#409 shape: a native <dialog> with bare
# select/input fields, a role="switch" toggle, a body-level danger Delete,
# and a plain type="button" Save — no <form>. Before #342 this returned NA
# for every modal-* check; it must now be held to the full contract.

FORMLESS_MODAL_HTML = """
<dialog id="overrideDialog" class="detail-dialog">
  <div class="detail-card">
    <div class="detail-header">
      <h2 id="overrideTitle">Override</h2>
      <button id="overrideClose" type="button" class="detail-close hit-target" aria-label="Close">
        <svg class="icon"><use href="#i-x"></use></svg>
      </button>
    </div>
    <div class="row"><span>Enabled</span>
      <button id="overrideEnabled" type="button" class="toggle on" role="switch" aria-checked="true"></button>
    </div>
    <label class="row"><span>Detector</span><select id="overrideZone" class="select-native"></select></label>
    <label class="row"><span>Bypass after</span><select id="overrideRetries" class="select-native"></select></label>
    <button id="overrideDelete" type="button" class="schedule-editor-delete" hidden>Delete override</button>
    <div class="detail-actions"><button id="overrideSave" type="button" class="detail-save-btn">Save</button></div>
  </div>
</dialog>
"""
FORMLESS_MODAL_CSS = """
.detail-dialog { margin-top: 16px; max-height: calc(100dvh - 32px); overflow-y: auto; }
.detail-card .row { display: flex; padding: 12px 0; border-top: 1px solid var(--line-muted); }
.detail-actions { display: flex; flex-direction: column; align-items: stretch; }
.detail-save-btn { background: var(--accent); color: var(--accent-fg); width: 100%; min-height: 48px; }
"""
formless = run_contracts(FORMLESS_MODAL_CSS, FORMLESS_MODAL_HTML)
for _cid, _why in (
        ("modal-unstyled-rows", ".detail-card .row is dialog-scoped via the wrapper class"),
        ("modal-raw-fieldset", "no fieldset"),
        ("modal-header", "h2 + aria-label=Close close button"),
        ("modal-footer", "one full-width solid-accent Save"),
        ("modal-top-anchor", "max-height + overflow-y on .detail-dialog")):
    check(formless[_cid]["status"] == "PASS",
          f"{_cid}: form-less #409-style editor is evaluated ({_why}) -> PASS, not NA (#342)")

# a live-control dialog (fields, action rails, NO Save) is not a *staged*
# editor: the footer contract is NA for it — its 5-button control rail must
# not be misread as a persistence footer (the home-automation camera live
# view; #342).
CONTROL_DIALOG_HTML = """
<dialog id="liveDialog" class="detail-dialog">
  <div class="detail-card">
    <div class="detail-header"><h2>Camera</h2>
      <button type="button" class="detail-close" aria-label="Close">x</button></div>
    <div class="live-actions">
      <button type="button" class="range-tab">Step</button>
      <button type="button" class="range-tab">-</button>
      <button type="button" class="range-tab">+</button>
      <button type="button" class="range-tab">Snapshot</button>
      <button type="button" class="range-tab">Record</button>
    </div>
    <label class="row"><span>Pan</span><input type="number" class="input-native"></label>
  </div>
</dialog>
"""
control_dlg = run_contracts(FORMLESS_MODAL_CSS, CONTROL_DIALOG_HTML)
check(control_dlg["modal-footer"]["status"] == "NA",
      "modal-footer: live-control dialog (fields, no Save) -> NA, rail not a footer (#342)")
check(control_dlg["modal-header"]["status"] == "PASS",
      "modal-header: live-control dialog still held to the header contract")


# ---- #342: hit-target contract (static leg) ----

HIT_SPEC = {"icons.size.inline": "16px", "components.hit-target.min": "44px"}

# no components.hit-target token in the spec -> NA (spec-driven, no hardcoded floor)
check(no_dialog["hit-target"]["status"] == "NA",
      "hit-target: token absent from spec -> NA")

# compact 34px controls mitigated by (a) the co-applied .hit-target utility
# and (b) a per-control ::before expansion; a real-geometry 44px control needs nothing
HIT_OK_CSS = """
.weather-icon-btn { width: 34px; height: 34px; }
.hit-target { position: relative; }
.hit-target::before { content: ""; position: absolute; inset: -5px; }
.detail-close { width: 34px; height: 34px; position: relative; }
.detail-close::before { content: ""; position: absolute; inset: -5px; }
.day-btn { width: 44px; height: 44px; }
"""
HIT_OK_HTML = '<button class="weather-icon-btn hit-target"></button><button class="detail-close"></button><button class="day-btn"></button>'
hit_ok = run_contracts(HIT_OK_CSS, HIT_OK_HTML, spec_light=HIT_SPEC)
check(hit_ok["hit-target"]["status"] == "PASS",
      "hit-target: 34px controls with utility/pseudo expansion + 44px real geometry -> PASS")

# a compact control with no expansion anywhere -> WARN naming the selector
HIT_BAD_CSS = ".tiny-btn { width: 30px; height: 30px; }"
hit_bad = run_contracts(HIT_BAD_CSS, '<button class="tiny-btn"></button>', spec_light=HIT_SPEC)
check(hit_bad["hit-target"]["status"] == "WARN",
      "hit-target: 30x30 control, no ::before expansion, no utility -> WARN")
check(".tiny-btn" in hit_bad["hit-target"]["detail"],
      "hit-target WARN names the offending selector")

# no fixed-size compact pointer targets authored at all -> NA even with the token
hit_na = run_contracts(GOOD_CSS, spec_light=HIT_SPEC)
check(hit_na["hit-target"]["status"] == "NA",
      "hit-target: no fixed-size compact control rules -> NA")


# ---- #342: chart contracts (static leg) ----

# no Chart.js at all -> both chart checks NA
check(no_dialog["chart-tick-budget"]["status"] == "NA", "chart-tick-budget: no Chart.js -> NA")
check(no_dialog["chart-noncolor-cue"]["status"] == "NA", "chart-noncolor-cue: no Chart.js -> NA")

CHART_GOOD_JS = """
const chart = new Chart(canvas, {
  data: { datasets: [
    { label: 'Generation', borderColor: pal.gen, borderDash: [], pointStyle: 'circle' },
    { label: 'Grid', borderColor: pal.grid, borderDash: [8, 4], pointStyle: 'rectRot' },
  ]},
  options: { scales: { x: { ticks: {
    maxRotation: 0, autoSkip: true, autoSkipPadding: 12, maxTicksLimit: budget(w),
  }}}},
});
"""
chart_good = run_contracts(GOOD_CSS, js=CHART_GOOD_JS)
check(chart_good["chart-tick-budget"]["status"] == "PASS",
      "chart-tick-budget: maxTicksLimit + autoSkip + maxRotation -> PASS")
check(chart_good["chart-noncolor-cue"]["status"] == "PASS",
      "chart-noncolor-cue: borderDash + pointStyle on coloured datasets -> PASS")

CHART_BAD_JS = """
const chart = new Chart(canvas, {
  data: { datasets: [
    { label: 'A', borderColor: '#0969da' },
    { label: 'B', borderColor: '#1a7f37' },
  ]},
  options: { scales: { x: { ticks: { color: '#888' } } } },
});
"""
chart_bad = run_contracts(GOOD_CSS, js=CHART_BAD_JS)
check(chart_bad["chart-tick-budget"]["status"] == "WARN",
      "chart-tick-budget: Chart.js with no maxTicksLimit/autoSkip -> WARN")
check(chart_bad["chart-noncolor-cue"]["status"] == "WARN",
      "chart-noncolor-cue: two coloured datasets, colour-only -> WARN")

CHART_SINGLE_JS = "new Chart(canvas, { data: { datasets: [{ borderColor: '#0969da' }] } });"
chart_single = run_contracts(GOOD_CSS, js=CHART_SINGLE_JS)
check(chart_single["chart-noncolor-cue"]["status"] == "NA",
      "chart-noncolor-cue: single coloured dataset -> NA (no second series to distinguish)")


# ---- #342: async-lifecycle contract (static leg) ----

# no data-state anywhere -> NA
check(no_dialog["async-lifecycle"]["status"] == "NA", "async-lifecycle: no data-state -> NA")

# shadcn-style interaction states are a different channel -> NA, not a finding
lc_interaction = run_contracts(GOOD_CSS, '<div data-state="open"></div><div data-state="closed"></div>')
check(lc_interaction["async-lifecycle"]["status"] == "NA",
      "async-lifecycle: interaction-only data-state (open/closed) -> NA")

# canonical vocabulary + role=status live region -> PASS (values may come from
# markup, JS literals, or CSS attribute selectors)
LC_GOOD_HTML = ('<main data-state="loading"></main>'
                '<div id="toast" role="status" aria-live="polite" hidden></div>')
LC_GOOD_CSS = GOOD_CSS + '\n[data-state="stale"] .note { color: var(--fg-muted); }\n'
LC_GOOD_JS = "pane.dataset.state = 'error';"
lc_good = run_contracts(LC_GOOD_CSS, LC_GOOD_HTML, js=LC_GOOD_JS)
check(lc_good["async-lifecycle"]["status"] == "PASS",
      "async-lifecycle: loading/stale/error within vocabulary + role=status -> PASS")
check("error" in lc_good["async-lifecycle"]["detail"],
      "async-lifecycle PASS lists the states it found")

# lifecycle vocabulary mixed with a non-canonical synonym -> WARN naming it
lc_mixed = run_contracts(GOOD_CSS, '<main data-state="loading"></main><section data-state="busy"></section>'
                                   '<div role="status"></div>')
check(lc_mixed["async-lifecycle"]["status"] == "WARN",
      "async-lifecycle: 'busy' outside the five-state vocabulary -> WARN")
check("busy" in lc_mixed["async-lifecycle"]["detail"],
      "async-lifecycle WARN names the stray value")

# lifecycle states with no role=status live region -> WARN
lc_silent = run_contracts(GOOD_CSS, '<main data-state="loading"></main>')
check(lc_silent["async-lifecycle"]["status"] == "WARN",
      "async-lifecycle: lifecycle states but no role=status live region -> WARN")

# ---- #416: the live region may be set from JS, not just declared in markup ----

# The real app-launcher board.js drawer: both halves of the contract are set
# through the DOM API. The check already read `dataset.state = '...'` for the
# lifecycle values, so demanding a markup-literal role="status" made every
# JS-rendered surface — exactly what this contract is for — unpassable.
LC_JS_DOM = ("exchange.dataset.state = 'loading';\n"
             "exchange.setAttribute('role', 'status');\n"
             "exchange.setAttribute('aria-live', 'polite');\n")
lc_jsdom = run_contracts(GOOD_CSS, js=LC_JS_DOM)
check(lc_jsdom["async-lifecycle"]["status"] == "PASS",
      "async-lifecycle: setAttribute('role', 'status') satisfies the live "
      "region (#416, real app-launcher board.js:222-224)")

# the reflected IDL property spelling, and irregular whitespace
check(run_contracts(GOOD_CSS, js="p.dataset.state = 'error';\np.role = 'status';"
                    )["async-lifecycle"]["status"] == "PASS",
      "async-lifecycle: the `el.role = 'status'` IDL spelling also counts (#416)")
check(run_contracts(GOOD_CSS, js='x.dataset.state="ready";\n'
                    'x.setAttribute( "role" , "status" );'
                    )["async-lifecycle"]["status"] == "PASS",
      "async-lifecycle: setAttribute spacing/quote style is not load-bearing (#416)")

# a JS surface that sets lifecycle state but no live region at all still WARNs —
# widening the spellings must not turn the check into a rubber stamp.
check(run_contracts(GOOD_CSS, js="pane.dataset.state = 'loading';"
                    )["async-lifecycle"]["status"] == "WARN",
      "async-lifecycle: JS lifecycle with no live region in any spelling still WARNs (#416)")
check(run_contracts(GOOD_CSS, js="pane.dataset.state = 'loading';\n"
                    "pane.setAttribute('role', 'alert');"
                    )["async-lifecycle"]["status"] == "WARN",
      "async-lifecycle: a different ARIA role is not a status live region (#416)")

_h.report_and_exit("test_modal_hit_target_contracts")
