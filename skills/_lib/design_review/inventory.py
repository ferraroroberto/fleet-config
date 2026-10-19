"""Cross-app inventory comparator for `/design-review fleet` (fleet-config#1500, Step 5/6 of #1383).

Compares rather than measures. It reads the newest `inventory` section per app
(Step 1/6, #1497: every screen of a walk, live, synthetic or e2e alike) and lists
every place the fleet renders one thing more than one way:

  INV-01  a text role rendered with more than one `{family, px, weight}`, or off design.md's value for it
  INV-02  a normalised action rendered by more than one component or variant
  INV-03  a bespoke control whose kind has a vendored component
  INV-04  one kind of choice, at an equivalent option count, rendered as different controls

**Reference** ("the one right way") per group. design.md's value is used where it has one: the type roles
from `typography.*`, and for choices 2-5 options -> `segmented`, 6+ -> `select` (design.md's
segmented-control contract; `range-tab` is retired). Otherwise INV-02 uses the vendored rendering most
apps use, and failing that the rendering most apps use. A tie leaves no reference, and every app counts as
divergent. An app is **divergent** when it renders anything other than the reference.

**Routing.** With one divergent app the finding has scope `app` and goes to that app's managed
`design-review` issue. With two or more it has scope `fleet` and goes to the digest and fleet-config's
issue. INV-03 counts the apps that carry the bespoke control. Every line's fix path is a vendored
component plus `/propagate-vendored`, never per-app restyling.

**Normalising, DOM first.** It reads the control's `kind` and `component` from the inventory, then matches
a short masked label against a fixed vocabulary (`ACTION_VOCAB`, `CHOICE_VOCAB`). A label longer than
`ACTION_LABEL_MAX_WORDS` is a whole-row tap whose label is the row's own text (#1500's note on #1497), so it
is never matched. A control whose component is `unknown` (an inventory taken with no catalog) is compared
for nothing but its choice kind. A select or combobox counts its options only when the entry carries
`options` (#1505); an inventory taken before that lists it as unclassified. A segmented or range-tab
group's option count is its entries on that screen, one group per component and `group` key per screen
(#1505; an inventory with no `group` merges them into one group per component, as before). Whatever the DOM cannot decide is listed
under `unclassified` for Step 6's per-app readers. This module never guesses.

**Filing-safe.** A filing line carries finding ids, counts, app names, renderings (family, px, weight,
component) and screen ids. It never carries a label, a control key, captured text or a screenshot path.
Masked labels and keys stay in the local digest JSON (`unclassified`).

Deterministic by construction: every collection is sorted and nothing is timed, so the same inventories
give byte-identical output.

The digest's `inventory` section (present only when at least one app has an inventory):

    {"schema_version": 1,
     "apps":         [{"target", "run", "source", "screens", "uncatalogued_screens"}],
     "missing":      [target],                      # no measured inventory in any run
     "catalog":      {"components": [name], "error": str|null},
     "counts":       {"INV-01": n, .., "app": n, "fleet": n},
     "findings":     [{"id", "class", "group", "scope", "reference", "reference_source",
                       "renderings": [{"rendering", "apps", "count"}], "apps", "divergent",
                       "examples": {app: screen id}, "counts": {app: n}}],
     "unclassified": [{"app", "kind", "component", "key", "label", "screen", "reason", "n"}]}

Public surface:

    app_inventory(target, metrics, run, source)      -> dict | None
    newest_inventory(target, state_root=None)        -> dict | None
    type_reference(spec)                             -> {role: "family Npx/weight"}
    compare(apps, reference, catalog, catalog_error, targets) -> the `inventory` section
    summaries(section, scope, target=None)           -> [filing summary]
    carry_statuses(existing_body, section, target)   -> {INV id: pass | fail | unmeasured}
    render_section(section)                          -> html

stdlib only.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hooks_state  # noqa: E402

from . import filing, report  # noqa: E402

SCHEMA_VERSION = 1
CLASSES = ("INV-01", "INV-02", "INV-03", "INV-04")
SEVERITY = "P2"
FLEET_OWNER = "fleet"

# A text role -> the design.md typography token holding its value, plus an optional weight override token.
ROLE_TOKENS: Dict[str, Tuple[str, Optional[str]]] = {
    "heading-1": ("heading-xl", None),
    "heading-2": ("heading-lg", None),
    "heading-3": ("heading-md", None),
    "row-title": ("body", "components.action-row.titleWeight"),
    "secondary": ("body-sm", None),
    "button-label": ("label", None),
    "caption": ("caption", None),
    "overline": ("overline", None),
    "body": ("body", None),
}
ROOT_PX = 16.0

# A short label names its action through one of these words; first match wins, so destructive outranks commit.
ACTION_LABEL_MAX_WORDS = 4
ACTION_KINDS = ("button", "icon-button", "link", "menuitem")
ACTION_VOCAB: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("destructive", re.compile(r"\b(delete|remove|discard|erase|purge|revoke|trash|uninstall|wipe|reset)\b")),
    ("dismiss", re.compile(r"\b(cancel|close|dismiss|not now)\b")),
    ("commit", re.compile(r"\b(save|apply|confirm|done|ok|submit|create|add|send)\b")),
    ("sort", re.compile(r"\b(sort|order by)\b")),
    ("filter", re.compile(r"\bfilters?\b")),
    ("refresh", re.compile(r"\b(refresh|reload|retry)\b")),
    ("settings", re.compile(r"\bsettings\b")),
    ("theme", re.compile(r"\b(theme|dark mode|light mode)\b")),
    ("overflow menu", re.compile(r"\b(more|menu|actions|options)\b")),
    ("edit", re.compile(r"\b(edit|rename)\b")),
    ("search", re.compile(r"\bsearch\b")),
    ("copy", re.compile(r"\bcopy\b")),
)
ROW_COMPONENT = "vendored:action-row"

# A bespoke control of this kind has this vendored equivalent, when the catalog carries it.
KIND_COMPONENT: Dict[str, str] = {
    "button": "button",
    "icon-button": "icon-button",
    "switch": "switch",
    "segmented": "segmented",
    "select": "select-native",
    "menuitem": "row-menu",
}

# Choices: what they choose (masked option or control labels), and design.md's control per option count.
CHOICE_KINDS = ("select", "combobox", "segmented", "range-tab")
GROUP_CHOICE_KINDS = ("segmented", "range-tab")
CHOICE_VOCAB: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("time range", re.compile(r"\b(day|days|week|weeks|month|months|year|years|today|yesterday|hour|hours|range|period)\b"
                              r"|(?:^|\s)#\s?[hdwmy]\b")),
    ("sort", re.compile(r"\b(sort|order)\b")),
    ("theme", re.compile(r"\b(theme|light|dark)\b")),
)
FEW_OPTIONS_MAX = 5
CHOICE_REFERENCE = {"2-5": "segmented", "6-plus": "select"}

R_SHORT_NO_WORD = "no action word in a short label"
R_LONG_LABEL = "long label: a whole-row tap, read by Step 6"
R_EMPTY_LABEL = "no label"
R_NO_OPTIONS = "option count not in the inventory"

_STAMP_RE = re.compile(r"^\d{8}T\d{6}Z")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ---- input -------------------------------------------------------------------------


def app_inventory(target: str, metrics: dict, run: Optional[str] = None, source: Optional[str] = None) -> Optional[dict]:
    """One app's measured inventory screens from a `metrics.json` document, or None when it has none.

    A screen whose `inventory` errored (`{"error": ..}`) or is absent is left out; one taken with no
    component catalog (`unmeasured` set) is kept, its controls read `unknown`."""
    screens = []
    for s in metrics.get("screens") or []:
        inv = (s.get("metrics") or {}).get("inventory") if isinstance(s, dict) else None
        if isinstance(inv, dict) and "error" not in inv and ("controls" in inv or "text" in inv):
            screens.append({"id": str(s.get("id")), "inventory": inv})
    if not screens:
        return None
    return {"target": target, "run": run, "source": source, "screens": sorted(screens, key=lambda s: s["id"])}


def newest_inventory(target: str, state_root: Optional[Path] = None) -> Optional[dict]:
    """The newest run under `<state>/design-review/<target>/` whose `metrics.json` carries an inventory."""
    root = Path(state_root) if state_root else hooks_state.state_dir() / "design-review" / target
    if not root.is_dir():
        return None
    for run_dir in sorted((p for p in root.iterdir() if p.is_dir() and _STAMP_RE.match(p.name)), key=lambda p: p.name, reverse=True):
        path = run_dir / "metrics.json"
        try:
            metrics = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = app_inventory(target, metrics, run_dir.name, str(path)) if isinstance(metrics, dict) else None
        if found:
            return found
    return None


def _px(text: object) -> Optional[float]:
    m = re.match(r"^\s*([0-9.]+)\s*(px|rem|em)?\s*$", str(text or ""))
    if not m:
        return None
    value = float(m.group(1))
    return value * ROOT_PX if m.group(2) in ("rem", "em") else value


def _family(text: object) -> str:
    return str(text or "").split(",")[0].strip().strip("\"'")


def _weight(text: object) -> str:
    s = str(text or "").strip()
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def _variant(family: object, px: object, weight: object) -> str:
    value = _px(px)
    size = f"{value:g}px" if value is not None else f"{px}px"
    return f"{_family(family) or 'unknown'} {size}/{_weight(weight)}"


def type_reference(spec: Dict[str, str]) -> Dict[str, str]:
    """design.md's rendering per text role, as `"family Npx/weight"`; a role whose token is missing has none."""
    out: Dict[str, str] = {}
    for role, (token, weight_token) in ROLE_TOKENS.items():
        base = f"typography.{token}"
        family, size, weight = spec.get(f"{base}.fontFamily"), spec.get(f"{base}.fontSize"), spec.get(f"{base}.fontWeight")
        if weight_token and spec.get(weight_token):
            weight = spec[weight_token]
        if family and _px(size) is not None and weight:
            out[role] = _variant(family, size, weight)
    return out


# ---- normalising -------------------------------------------------------------------


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def action_of(control: dict) -> Tuple[Optional[str], Optional[str]]:
    """`(action, None)` when the DOM decides it, else `(None, why)`."""
    if control.get("component") == ROW_COMPONENT:
        return "row open", None
    label = str(control.get("label") or "").lower().strip()
    words = label.split()
    if not words:
        return None, R_EMPTY_LABEL
    if len(words) > ACTION_LABEL_MAX_WORDS:
        return None, R_LONG_LABEL
    for name, rx in ACTION_VOCAB:
        if rx.search(label):
            return name, None
    return None, R_SHORT_NO_WORD


def choice_of(labels: Iterable[str]) -> str:
    text = " ".join(str(x or "").lower() for x in labels)
    for name, rx in CHOICE_VOCAB:
        if rx.search(text):
            return name
    return "other"


def _bucket(options: int) -> str:
    return "2-5" if options <= FEW_OPTIONS_MAX else "6-plus"


def _rendering(control: dict) -> str:
    comp, kind = str(control.get("component")), str(control.get("kind"))
    name = comp if comp == f"vendored:{kind}" else f"{comp} {kind}"
    if kind == "icon-button":
        return name
    return f"{name} {_variant(control.get('family'), control.get('px'), control.get('weight')).split(' ', 1)[1]}"


# ---- comparing ---------------------------------------------------------------------

Seen = Dict[str, Dict[str, Dict[str, str]]]   # group -> rendering -> app -> first screen id


def _note(into: Seen, group: str, rendering: str, app: str, screen: str) -> None:
    apps = into.setdefault(group, {}).setdefault(rendering, {})
    apps[app] = min(apps.get(app, screen), screen)


def _majority(renderings: Dict[str, Dict[str, str]], pool: Optional[Iterable[str]] = None) -> Optional[str]:
    cands = sorted(pool if pool is not None else renderings)
    if not cands:
        return None
    counts = {r: len(renderings[r]) for r in cands}
    top = max(counts.values())
    best = [r for r in cands if counts[r] == top]
    return best[0] if len(best) == 1 else None


def _finding(cls: str, group: str, renderings: Dict[str, Dict[str, str]], reference: Optional[str],
             source: Optional[str]) -> Optional[dict]:
    divergent = sorted({a for r, apps in renderings.items() if r != reference for a in apps})
    if not divergent:
        return None
    examples: Dict[str, str] = {}
    for app in divergent:
        examples[app] = min(apps[app] for r, apps in renderings.items() if r != reference and app in apps)
    rows = sorted(({"rendering": r, "apps": sorted(apps), "count": len(apps)} for r, apps in renderings.items()),
                  key=lambda x: (-x["count"], x["rendering"]))
    return {"id": f"{cls}-{_slug(group)}", "class": cls, "group": group,
            "scope": "app" if len(divergent) == 1 else "fleet",
            "reference": reference, "reference_source": source if reference else None,
            "renderings": rows, "apps": sorted({a for apps in renderings.values() for a in apps}),
            "divergent": divergent, "examples": examples, "counts": {}}


def compare(apps: List[dict], reference: Dict[str, str], catalog: Optional[Iterable[str]] = None,
            catalog_error: Optional[str] = None, targets: Optional[Iterable[str]] = None) -> dict:
    """Pure: the digest's `inventory` section from each app's `app_inventory` result."""
    apps = sorted((a for a in apps if a), key=lambda a: a["target"])
    text: Seen = {}
    actions: Seen = {}
    choices: Seen = {}
    bespoke: Dict[str, Dict[str, dict]] = {}
    unclassified: Dict[str, dict] = {}
    observed_vendored = set()
    app_rows = []

    def unclass(app: str, screen: str, c: dict, why: str, n: int) -> None:
        entry = {"app": app, "kind": str(c.get("kind")), "component": str(c.get("component")), "key": str(c.get("key") or ""),
                 "label": str(c.get("label") or ""), "screen": screen, "reason": why, "n": 0}
        k = json.dumps([entry[f] for f in ("app", "kind", "component", "key", "label", "reason")])
        cur = unclassified.setdefault(k, entry)
        cur["screen"] = min(cur["screen"], screen)
        cur["n"] += n

    for app in apps:
        t = app["target"]
        uncatalogued = 0
        for screen in sorted(app["screens"], key=lambda s: s["id"]):
            sid, inv = screen["id"], screen["inventory"]
            if inv.get("unmeasured"):
                uncatalogued += 1
            for tr in inv.get("text") or []:
                for v in tr.get("variants") or []:
                    _note(text, str(tr.get("role")), _variant(v.get("family"), v.get("px"), v.get("weight")), t, sid)
            groups: Dict[Tuple[str, str, str], dict] = {}
            for c in inv.get("controls") or []:
                kind, comp, n = str(c.get("kind")), str(c.get("component")), int(c.get("n") or 1)
                if comp.startswith("vendored:"):
                    observed_vendored.add(comp.split(":", 1)[1])
                if kind in GROUP_CHOICE_KINDS:
                    g = groups.setdefault((kind, comp, str(c.get("group") or "")), {"labels": [], "n": 0, "options": None})
                    g["labels"].append(str(c.get("label") or ""))
                    g["n"] += n
                    if isinstance(c.get("options"), int):
                        g["options"] = c["options"]
                elif kind in ("select", "combobox"):
                    if isinstance(c.get("options"), int) and not isinstance(c.get("options"), bool):
                        _note(choices, f"{choice_of([c.get('label')])}|{_bucket(c['options'])}", kind, t, sid)
                    else:
                        unclass(t, sid, c, R_NO_OPTIONS, n)
                if comp == "unknown":
                    continue
                if comp == "bespoke" and kind in KIND_COMPONENT:
                    b = bespoke.setdefault(kind, {}).setdefault(t, {"n": 0, "screen": sid})
                    b["n"] += n
                    b["screen"] = min(b["screen"], sid)
                if kind in ACTION_KINDS:
                    action, why = action_of(c)
                    if action is None:
                        unclass(t, sid, c, str(why), n)
                    else:
                        _note(actions, action + (" in menu" if kind == "menuitem" else ""), _rendering(c), t, sid)
            for (kind, _comp, _group), g in sorted(groups.items()):
                options = g["options"] if g["options"] is not None else g["n"]
                _note(choices, f"{choice_of(sorted(g['labels']))}|{_bucket(options)}", kind, t, sid)
        app_rows.append({"target": t, "run": app.get("run"), "source": app.get("source"),
                         "screens": len(app["screens"]), "uncatalogued_screens": uncatalogued})

    findings: List[dict] = []
    for role in sorted(text):
        ref = reference.get(role)
        f = _finding("INV-01", role, text[role], ref or _majority(text[role]), "design.md" if ref else "most apps")
        if f:
            findings.append(f)
    for action in sorted(actions):
        r = actions[action]
        vendored = [x for x in r if x.startswith("vendored:")]
        ref = _majority(r, vendored) if vendored else _majority(r)
        f = _finding("INV-02", action, r, ref, "most apps, vendored" if vendored else "most apps")
        if f:
            findings.append(f)

    names = set(catalog or ()) | observed_vendored
    for kind in sorted(bespoke):
        component = KIND_COMPONENT[kind]
        if component not in names:
            continue
        per_app = bespoke[kind]
        divergent = sorted(per_app)
        findings.append({"id": f"INV-03-{_slug(kind)}", "class": "INV-03", "group": kind,
                         "scope": "app" if len(divergent) == 1 else "fleet", "reference": f"vendored:{component}",
                         "reference_source": "catalog" if component in set(catalog or ()) else "observed",
                         "renderings": [{"rendering": f"bespoke {kind}", "apps": divergent, "count": len(divergent)}],
                         "apps": divergent, "divergent": divergent,
                         "examples": {a: per_app[a]["screen"] for a in divergent},
                         "counts": {a: per_app[a]["n"] for a in divergent}})

    for key in sorted(choices):
        choice, bucket = key.split("|", 1)
        f = _finding("INV-04", f"{choice} {bucket}", choices[key], CHOICE_REFERENCE[bucket], "design.md")
        if f:
            f["group"] = f"{choice} ({bucket.replace('-plus', '+')} options)"
            findings.append(f)

    findings.sort(key=lambda f: (f["class"], f["id"]))
    counts = {cls: sum(1 for f in findings if f["class"] == cls) for cls in CLASSES}
    counts.update({scope: sum(1 for f in findings if f["scope"] == scope) for scope in ("app", "fleet")})
    present = {a["target"] for a in apps}
    return {
        "schema_version": SCHEMA_VERSION,
        "apps": app_rows,
        "missing": sorted(set(targets or ()) - present),
        "catalog": {"components": sorted(set(catalog or ())), "error": catalog_error if catalog is None else None},
        "counts": counts,
        "findings": findings,
        "unclassified": sorted(unclassified.values(), key=lambda u: (u["app"], u["reason"], u["kind"], u["component"], u["key"], u["label"])),
    }


# ---- filing ------------------------------------------------------------------------

TITLES = {
    "INV-01": "text role `{group}` renders {ways}",
    "INV-02": "action `{group}` renders {ways}",
    "INV-03": "bespoke `{group}` where a vendored one exists",
    "INV-04": "choice `{group}` renders as {controls}",
}
FIXES = {
    "INV-01": "fix: the role's type token in the vendored base + /propagate-vendored",
    "INV-02": "fix: one vendored component per action + /propagate-vendored",
    "INV-03": "fix: the vendored `{component}` + /propagate-vendored",
    "INV-04": "fix: the vendored `{reference}` for this option count (design.md) + /propagate-vendored",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def detail(f: dict) -> str:
    """The line's counts, renderings and one example screen id per divergent app: nothing captured."""
    if f["class"] == "INV-03":
        total = sum(f["counts"].values())
        lead = (f"{_plural(total, 'control')} in {_plural(len(f['apps']), 'app')}: "
                + ", ".join(f"{a} {f['counts'][a]}" for a in f["apps"]))
    else:
        lead = "; ".join(f"{r['rendering']} in {_plural(r['count'], 'app')} ({', '.join(r['apps'])})" for r in f["renderings"])
        ref = f.get("reference")
        lead += f" · reference {ref}, from {f['reference_source']}" if ref else " · no reference: a tie"
    return f"{lead} · e.g. " + ", ".join(f"{a}: {f['examples'][a]}" for a in f["divergent"])


def summary(f: dict) -> dict:
    """One finding as a `filing.finding_line` summary."""
    n = len(f["renderings"])
    component = str(f.get("reference") or "").split(":", 1)[-1]
    return {"id": f["id"], "severity": SEVERITY, "owner": "app" if f["scope"] == "app" else FLEET_OWNER,
            "title": TITLES[f["class"]].format(group=f["group"], ways=_plural(n, "way"), controls=_plural(n, "control")),
            "standard": FIXES[f["class"]].format(component=component, reference=KIND_COMPONENT.get(component, component)),
            "apps": list(f["divergent"]), "detail": detail(f)}


def summaries(section: Optional[dict], scope: str, target: Optional[str] = None) -> List[dict]:
    """Filing summaries for one destination: scope `app` for `target`'s issue, scope `fleet` for the digest's."""
    if not section:
        return []
    out = []
    for f in section.get("findings") or []:
        if f["scope"] != scope or (scope == "app" and f["divergent"] != [target]):
            continue
        out.append(summary(f))
    return out


def carry_statuses(existing_body: str, section: Optional[dict], target: Optional[str] = None) -> Dict[str, str]:
    """Statuses for the `INV-*` lines already on a body, so the merge tags them right when they are not current.

    `fail` = still a finding but filed elsewhere this run (carried); `pass` = the inventory was compared and
    the divergence is gone (fixed); `unmeasured` = no inventory to compare this run (carried)."""
    ids = []
    for line in filing.parse_body(existing_body or "")["sections"].get(filing.SEC_FINDINGS) or []:
        p = filing.parse_line(line)
        if p and p["id"].startswith("INV-"):
            ids.append(p["id"])
    if not ids:
        return {}
    findings = {f["id"]: f for f in (section or {}).get("findings") or []}
    measured = {a["target"] for a in (section or {}).get("apps") or []}
    here = bool(measured) if target is None else target in measured
    out: Dict[str, str] = {}
    for rid in ids:
        f = findings.get(rid)
        if f and (target is None or target in f["divergent"]):
            out[rid] = "fail"
        else:
            out[rid] = "pass" if here else "unmeasured"
    return out


# ---- rendering ---------------------------------------------------------------------


def render_section(section: dict) -> str:
    """The digest page's Inventory section."""
    c = section.get("counts") or {}
    head = " · ".join(f"{cls} {c.get(cls, 0)}" for cls in CLASSES) + f" — {c.get('app', 0)} single-app, {c.get('fleet', 0)} cross-app"
    rows = "".join(
        f'<tr><th scope="row">{report._esc(a["target"])}</th><td class="ids">{report._esc(a.get("run") or "—")}</td><td>{a["screens"]}</td>'
        f'<td>{a["uncatalogued_screens"]}</td><td>{sum(u["n"] for u in section.get("unclassified") or [] if u["app"] == a["target"])}</td></tr>'
        for a in section.get("apps") or [])
    table = ('<div class="tbl"><table><thead><tr><th>App</th><th>Inventory run</th><th>Screens</th><th>No catalog</th>'
             '<th>Unclassified</th></tr></thead><tbody>' + rows + "</tbody></table></div>")
    items = "".join(
        f'<li><span class="badge sev sev-{SEVERITY}">{SEVERITY}</span> <code class="rid">{report._esc(f["id"])}</code> '
        f'{report._esc(summary(f)["title"])} <span class="badge badge-unm">{report._esc("cross-app" if f["scope"] == "fleet" else "single-app")}</span> '
        f'<span class="muted small">— {report._esc(detail(f))}</span></li>' for f in section.get("findings") or [])
    found = f'<ul class="plain">{items}</ul>' if items else '<p class="muted">Every compared role, action and choice renders one way.</p>'
    missing = section.get("missing") or []
    cat = section.get("catalog") or {}
    notes = []
    if missing:
        notes.append(f"No inventory yet: {', '.join(missing)}.")
    if cat.get("error"):
        notes.append(f"Component catalog unavailable ({cat['error']}); INV-03 used the vendored components seen in the inventories.")
    note = f'<p class="muted small">{report._esc(" ".join(notes))}</p>' if notes else ""
    return report._section("inventory", "Inventory — cross-app comparison", f"<p>{report._esc(head)}</p>{found}{table}{note}",
                           "newest inventory per app; one divergent app files on its issue, two or more on the digest")
