"""Stage 3a of /design-review — load and validate `design.rubric.toml` (fleet-config#971).

The rubric is data, not prompt: every rule has a stable `id`, the metric it
reads, the comparison that makes it fail, a severity, the standard it cites,
a fix template, an owner and a mock-up id. Thresholds resolve from
`design.md` tokens where one exists (`threshold_token`, via
`design_lint/spec.py`'s `parse_spec`), else from the literal `threshold`.
Measurement floors the in-page script needs *before* it runs (the 44px
hit-target minimum, the 48px primary-button height) resolve the same way
through `[params]`.

Rubric file shape (flat, so #973 can append `[[judgment]]` entries without
restructuring):

    [meta]        version = "1.0.0"          # stamped on every metrics/evaluate output
    [weights]     <category> = <float>       # category weight in the overall score
    [grades]      A = 90, B = 80, ...        # minimum score per letter, F = 0
    [penalties]   P0 = 25, P1 = 12, P2 = 6, P3 = 2   # per failing rule, once per rule
    [params.<name>]  token = "components.hit-target.min", default = 44
    [[rules]]
      id, category, title, metric, fail_when, threshold, [threshold_token],
      severity, standard, fix_template, owner, mockup, [devices], [screens]

`fail_when` is one of `gt`, `gte`, `lt`, `lte`, `eq`, `ne`, `true`, `nonzero`
and reads as "the rule fails when `metric <fail_when> threshold`"; `true` and
`nonzero` ignore `threshold`. `metric` is a dotted per-screen path from
`measure.py` (`targets.small_count`) or a derived metric `evaluate.py`
computes (`targets.small_share`, `icons.off_step_count`, `spec.pairs_under_aa`).
`devices` / `screens` restrict where a rule applies (`screens` matches on the
screen `kind`: `tab`, `dialog`, `step`).

stdlib only.
"""
from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from design_lint.spec import parse_spec  # noqa: E402

DEFAULT_RUBRIC = Path(__file__).resolve().parent.parent.parent.parent / "design.rubric.toml"

SEVERITIES = ("P0", "P1", "P2", "P3")
OWNERS = ("spec", "scaffold", "app")
FAIL_WHEN = ("gt", "gte", "lt", "lte", "eq", "ne", "true", "nonzero")
KINDS = ("tab", "dialog", "step")

# Derived metrics evaluate.py computes from raw metrics + the spec. Listed
# here so rubric validation can name a typo instead of silently `unmeasured`.
DERIVED_METRICS = (
    "targets.small_share",
    "text.under14_share",
    "icons.off_step_count",
    "spec.pairs_under_aa",
    "text.uppercase_outside_role",
    "text.break_all_non_path",
    "layout.lists_unfiltered_tall",
    "layout.content_share",
    "a11y.zoom_locked_no_control",
    "nav.pane_header_hidden",
)


class RubricError(Exception):
    """A rubric that would make the evaluation lie is refused, not tolerated."""


@dataclass
class Rule:
    id: str
    category: str
    title: str
    metric: str
    fail_when: str
    threshold: Optional[float]
    severity: str
    standard: str
    fix_template: str
    owner: str
    mockup: str
    threshold_token: Optional[str] = None
    devices: List[str] = field(default_factory=list)
    screens: List[str] = field(default_factory=list)
    params: Dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, object]:
        return {
            "id": self.id, "category": self.category, "title": self.title, "metric": self.metric,
            "fail_when": self.fail_when, "threshold": self.threshold, "threshold_token": self.threshold_token,
            "severity": self.severity, "standard": self.standard, "fix_template": self.fix_template,
            "owner": self.owner, "mockup": self.mockup, "devices": list(self.devices),
            "screens": list(self.screens), "params": dict(self.params),
        }


@dataclass
class Rubric:
    version: str
    weights: Dict[str, float]
    grades: Dict[str, float]
    penalties: Dict[str, float]
    params: Dict[str, dict]
    rules: List[Rule]
    path: Optional[Path] = None

    @property
    def categories(self) -> List[str]:
        seen: List[str] = []
        for r in self.rules:
            if r.category not in seen:
                seen.append(r.category)
        return seen


_PX_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*(px)?\s*$")


def px_value(text: object) -> Optional[float]:
    """`"44px"` / `"44"` / `44` -> 44.0; anything else -> None."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    m = _PX_RE.match(str(text))
    return float(m.group(1)) if m else None


def load_rubric(path: Optional[Path] = None) -> Rubric:
    """Parse + validate the rubric; raise `RubricError` on anything ambiguous."""
    rubric_path = Path(path) if path else DEFAULT_RUBRIC
    try:
        data = tomllib.loads(rubric_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RubricError(f"cannot read rubric {rubric_path}: {exc}") from exc
    return validate_rubric(data, rubric_path)


def validate_rubric(data: dict, path: Optional[Path] = None) -> Rubric:
    meta = data.get("meta")
    if not isinstance(meta, dict) or not str(meta.get("version", "")).strip():
        raise RubricError("[meta].version is required")
    weights = {str(k): float(v) for k, v in (data.get("weights") or {}).items()}
    grades = {str(k): float(v) for k, v in (data.get("grades") or {}).items()}
    penalties = {str(k): float(v) for k, v in (data.get("penalties") or {}).items()}
    params = {str(k): dict(v) for k, v in (data.get("params") or {}).items() if isinstance(v, dict)}
    if not grades or "F" not in grades:
        raise RubricError("[grades] must map letters to minimum scores and include F")
    missing_pen = [s for s in SEVERITIES if s not in penalties]
    if missing_pen:
        raise RubricError(f"[penalties] missing {missing_pen}")
    raw_rules = data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise RubricError("[[rules]] must be a non-empty array")

    rules: List[Rule] = []
    ids: set = set()
    for i, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise RubricError(f"rules[{i}] is not a table")
        rid = str(raw.get("id", "")).strip()
        if not rid or rid in ids:
            raise RubricError(f"rules[{i}]: id missing or duplicate ({rid!r})")
        ids.add(rid)
        for key in ("category", "title", "metric", "fail_when", "severity", "standard", "fix_template", "owner", "mockup"):
            if key not in raw:
                raise RubricError(f"{rid}: missing field {key!r}")
        if raw["severity"] not in SEVERITIES:
            raise RubricError(f"{rid}: severity {raw['severity']!r} not in {SEVERITIES}")
        if raw["owner"] not in OWNERS:
            raise RubricError(f"{rid}: owner {raw['owner']!r} not in {OWNERS}")
        if raw["fail_when"] not in FAIL_WHEN:
            raise RubricError(f"{rid}: fail_when {raw['fail_when']!r} not in {FAIL_WHEN}")
        threshold = raw.get("threshold")
        if raw["fail_when"] not in ("true", "nonzero") and threshold is None and not raw.get("threshold_token"):
            raise RubricError(f"{rid}: fail_when={raw['fail_when']} needs a threshold or threshold_token")
        if raw["category"] not in weights:
            raise RubricError(f"{rid}: category {raw['category']!r} has no [weights] entry")
        for kind in raw.get("screens", []) or []:
            if kind not in KINDS:
                raise RubricError(f"{rid}: screens entry {kind!r} not in {KINDS}")
        rules.append(Rule(
            id=rid, category=str(raw["category"]), title=str(raw["title"]), metric=str(raw["metric"]),
            fail_when=str(raw["fail_when"]),
            threshold=float(threshold) if isinstance(threshold, (int, float)) and not isinstance(threshold, bool) else None,
            threshold_token=str(raw["threshold_token"]) if raw.get("threshold_token") else None,
            severity=str(raw["severity"]), standard=str(raw["standard"]), fix_template=str(raw["fix_template"]),
            owner=str(raw["owner"]), mockup=str(raw["mockup"]),
            devices=[str(d) for d in raw.get("devices", []) or []],
            screens=[str(s) for s in raw.get("screens", []) or []],
            params=dict(raw.get("params", {}) or {}),
        ))
    return Rubric(version=str(meta["version"]), weights=weights, grades=grades, penalties=penalties,
                  params=params, rules=rules, path=path)


def check_metric_names(rubric: Rubric, known_paths: List[str]) -> List[str]:
    """Rule ids whose `metric` is neither a script path nor a derived metric."""
    known = set(known_paths) | set(DERIVED_METRICS)
    return [r.id for r in rubric.rules if r.metric not in known]


def resolve_threshold(rule: Rule, spec: Dict[str, str]) -> Dict[str, object]:
    """`{value, source}` — the spec token when it resolves to a px number, else the literal."""
    if rule.threshold_token:
        raw = spec.get(rule.threshold_token)
        val = px_value(raw) if raw is not None else None
        if val is not None:
            return {"value": val, "source": f"spec:{rule.threshold_token}"}
        if rule.threshold is None:
            return {"value": None, "source": f"unresolved:{rule.threshold_token}"}
    return {"value": rule.threshold, "source": "rubric"}


def resolve_params(rubric: Rubric, spec: Dict[str, str]) -> Dict[str, object]:
    """Measurement floors from the spec (`[params.<name>].token`) with literal defaults.

    A `token` naming a group (`icons.size`) resolves to the sorted list of
    its children's px values — the canonical icon-size steps.
    """
    out: Dict[str, object] = {}
    for name, entry in rubric.params.items():
        token = entry.get("token")
        default = entry.get("default")
        resolved: object = None
        if token:
            if token in spec:
                resolved = px_value(spec[token])
            else:
                prefix = f"{token}."
                kids = [px_value(v) for k, v in spec.items() if k.startswith(prefix)]
                vals = sorted({v for v in kids if v is not None})
                resolved = vals or None
        out[name] = resolved if resolved is not None else default
    return out


def load_specs(spec_light: Optional[Path] = None, spec_dark: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """`{"light": tokens, "dark": tokens}` from `~/.claude/design*.md` or overrides.

    A missing file yields an empty token dict for that theme — thresholds
    then fall back to the rubric's literals and `spec.*` rules go
    `unmeasured`, which is honest, not fatal.
    """
    home = Path.home() / ".claude"
    paths = {"light": Path(spec_light) if spec_light else home / "design.md",
             "dark": Path(spec_dark) if spec_dark else home / "design.dark.md"}
    out: Dict[str, Dict[str, str]] = {}
    for theme, p in paths.items():
        try:
            out[theme] = parse_spec(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            out[theme] = {}
    return out
