"""Deterministic core of /design-review (fleet-config#970, step 1 = #971).

What `design_lint` structurally cannot see — rendered facts: computed font
sizes, WCAG contrast against the composited background, effective hit
rectangles, icon boxes, overflow, accessible names — measured in a real
browser against a *running* fleet app, scored against a versioned rubric,
with no LLM in the loop. The skill (#972) orchestrates; this package
measures and evaluates.

Two legs, two interpreters (orchestrator decision, fleet-config#971):

  measure   this repo's stdlib venv resolves the target and the run dir,
            probes the port, then spawns `walk.py` under the **target
            repo's** `.venv` interpreter (where Playwright lives), collects
            `screens.json`, and writes `metrics.json` + screenshots to a run
            directory under the gitignored hooks state dir, never a tracked tree.
  evaluate  pure stdlib: `metrics.json` + `design.rubric.toml` + the spec
            token files -> rule results and category grades, as JSON.

Modules:

  plan.py      target/port/scheme from hooks/projects.toml, `[design.review]`
               from the target's .fleet.toml, the fixed device matrix, screen ids
  capture.py   run dir, interpreter resolution, liveness probe, walk spawn, envelope
  walk.py      the Playwright child (tabs, details, dialogs, extra steps; screenshots)
  measure.py   the in-page measurement script + the per-screen metrics schema
  rubric.py    load/validate design.rubric.toml; thresholds + params from the spec
  evaluate.py  rule results, scoring formula, category + overall grades
  cli.py       argparse: `measure <repo>` / `evaluate <metrics.json>`

Stable interfaces for the next steps (#972 renders, #973 adds `[[judgment]]`,
#974 diffs by rule id):

  metrics.json     schema_version, rubric_version, target, commit, generated_at,
                   base_url, run_dir, devices, review, params{script,resolved},
                   interpreter, unmeasured{reason,detail}|null, walk, screens[]
                     screen: id, device, theme, view, kind, status, reason, error,
                             screenshot, screenshot_full, metrics{...measure.py}
  evaluate output  schema_version, rubric_version, target, commit, generated_at,
                   metrics_generated_at, base_url, run_dir, unmeasured, screens[],
                   rules[{id, category, severity, owner, status, reason, evidence,
                          measured, threshold, title, standard, fix_template, mockup}],
                   categories{cat: {score, grade, unmeasured, failed, unmeasured_rules}},
                   overall{score, grade, unmeasured}
  screen id        `<device>-<theme>-<view>` — `iphone-light-board`,
                   `desktop-dark-dialog-settings`

Read-only by construction: the walk clicks primary tabs, sets `details.open`,
calls `dialog.showModal()`/`close()`; anything more is an opt-in
`[design.review].extra_steps` click. Loopback only, scoped certificate bypass.
Screenshots stay in the run directory and are never attached to an issue,
PR or comment.
"""
from __future__ import annotations

from .capture import measure_target, probe_listening, resolve_interpreter, run_dir_for
from .cli import main
from .evaluate import evaluate, parse_color, spec_pairs
from .measure import SCHEMA_VERSION, build_script, default_params, metric_paths, metric_value
from .plan import DEVICES, Target, load_review_block, resolve_target, screen_id
from .rubric import Rubric, RubricError, load_rubric, load_specs, resolve_params, resolve_threshold, validate_rubric

__all__ = [
    "DEVICES", "Rubric", "RubricError", "SCHEMA_VERSION", "Target",
    "build_script", "default_params", "evaluate", "load_review_block", "load_rubric", "load_specs",
    "main", "measure_target", "metric_paths", "metric_value", "parse_color", "probe_listening",
    "resolve_interpreter", "resolve_params", "resolve_target", "resolve_threshold", "run_dir_for",
    "screen_id", "spec_pairs", "validate_rubric",
]
