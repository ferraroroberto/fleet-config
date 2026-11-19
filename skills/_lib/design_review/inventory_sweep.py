"""The digest's `inventory_sweep` section for `/design-review fleet inventory`: pure.

`fleet.run_inventory_capture` measures every web app with `--e2e` (the synthetic walk plus the app's own e2e suite
in capture mode) and `fleet.run_inventory_report` re-compares the saved runs with the readers' answers. This
module turns those runs into what the sweep reports beside the cross-app comparison (`inventory.py`):

  * one row per app: its surface-map entry count, the entries no screen showed, the overlays the run never
    opened (`NOT_WALKED`), the e2e outcome, the inventory's screen count and hash, and the repeat check;
  * the repeat check: the inventory hash against the previous inventory sweep of the same app. `identical` and
    `changed` compare two captures at the same commit; `commit changed` and `first` compare nothing. Only
    `identical` shows that a re-run on an unchanged tree gave the same inventory;
  * a summary of the comparison's divergences by text role, by action, by bespoke kind and by choice, with the
    count of divergences per app.

An app the sweep could not measure keeps its `unmeasured` reason and every count it could not establish is None
(printed `unknown`), never 0.

    {"phase": "capture" | "report",
     "apps": [{"target", "unmeasured", "e2e", "e2e_surfaces", "e2e_tree", "map_file", "map_error", "surfaces", "never_walked",
               "not_walked", "screens", "inventory_hash", "commit", "repeat", "previous_hash", "unclassified",
               "classified"}],
     "classification": {target: {"entries", "prompt", "answers", "answered", "problems"}},
     "summary": {"by_role", "by_action", "bespoke", "choices": [{"group", "id", "reference", "renderings", "divergent"}],
                 "per_app": {target: {"INV-01".."INV-04", "total"}}}}

Public surface:

    inventory_hash(app_inv)              -> str | None
    repeat_state(previous, commit, hash) -> "identical" | "changed" | "commit changed" | "first" | "unmeasured"
    app_row(row, doc, app_inv)           -> dict
    summary(section)                     -> dict
    render_section(sweep)                -> html

stdlib only.
"""
from __future__ import annotations

import hashlib
import json
from typing import Dict, Optional

from . import inventory, report

REPEAT_STATES = ("identical", "changed", "commit changed", "first", "unmeasured")
SUMMARY_CLASSES = {"INV-01": "by_role", "INV-02": "by_action", "INV-03": "bespoke", "INV-04": "choices"}


def inventory_hash(app_inv: Optional[dict]) -> Optional[str]:
    """A hash of the app's inventory screens (ids and inventories, key order normalised); None with no inventory."""
    if not app_inv:
        return None
    raw = json.dumps([{"id": s["id"], "inventory": s["inventory"]} for s in app_inv["screens"]], sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def repeat_state(previous: Optional[dict], commit: Optional[str], digest: Optional[str]) -> str:
    """This capture against the previous one recorded for the app (`{commit, hash}`)."""
    if not digest:
        return "unmeasured"
    if not previous or not previous.get("hash"):
        return "first"
    if not commit or previous.get("commit") != commit:
        return "commit changed"
    return "identical" if previous["hash"] == digest else "changed"


def _e2e_state(doc: dict) -> str:
    e2e = doc.get("e2e")
    if not isinstance(e2e, dict):
        return "not run"
    for key in ("unmeasured", "warning"):
        note = e2e.get(key)
        if isinstance(note, dict) and note.get("reason"):
            return str(note["reason"])
    return "ok"


def app_row(row: dict, doc: dict, app_inv: Optional[dict]) -> dict:
    """One app's sweep row from its fleet digest row, its evaluate document and its inventory."""
    cov = doc.get("overlay_coverage") if isinstance(doc.get("overlay_coverage"), dict) else {}
    sid = doc.get("surface_ids") if isinstance(doc.get("surface_ids"), dict) else {}
    smap = sid.get("map") if isinstance(sid.get("map"), dict) else {}
    never = sid.get("never_walked")
    e2e = doc.get("e2e") if isinstance(doc.get("e2e"), dict) else {}
    readable = bool(smap.get("file")) and not smap.get("error")
    return {
        "target": row["target"], "unmeasured": row.get("unmeasured"), "e2e": _e2e_state(doc),
        "e2e_surfaces": len(e2e.get("surfaces") or []) if e2e else None,
        # What the suite left in the target's working tree: `restore_failed` means a changed checkout, said out loud.
        "e2e_tree": (e2e.get("tree") or {}).get("state") if isinstance(e2e.get("tree"), dict) else None,
        "map_file": smap.get("file"), "map_error": smap.get("error"),
        "surfaces": int(smap.get("entries") or 0) if readable else None,
        "never_walked": len(never) if isinstance(never, list) else None,
        "not_walked": cov.get("not_walked_count") if cov.get("status") == "measured" else None,
        "screens": len(app_inv["screens"]) if app_inv else 0,
        "inventory_hash": inventory_hash(app_inv), "commit": row.get("commit"),
        "repeat": "unmeasured", "previous_hash": None, "unclassified": 0, "classified": 0,
    }


def summary(section: Optional[dict]) -> dict:
    """The comparison's divergences grouped by role, action, bespoke kind and choice, and counted per app."""
    out: Dict[str, object] = {name: [] for name in SUMMARY_CLASSES.values()}
    per_app: Dict[str, Dict[str, int]] = {}
    for a in (section or {}).get("apps") or []:
        per_app[a["target"]] = {cls: 0 for cls in inventory.CLASSES} | {"total": 0}
    for f in (section or {}).get("findings") or []:
        out[SUMMARY_CLASSES[f["class"]]].append({  # type: ignore[union-attr]
            "group": f["group"], "id": f["id"], "reference": f.get("reference"),
            "renderings": len(f["renderings"]), "divergent": list(f["divergent"])})
        for app in f["divergent"]:
            counts = per_app.setdefault(app, {cls: 0 for cls in inventory.CLASSES} | {"total": 0})
            counts[f["class"]] += 1
            counts["total"] += 1
    out["per_app"] = {k: per_app[k] for k in sorted(per_app)}
    return out


def _n(value: object) -> str:
    return "unknown" if value is None else str(value)


def render_section(sweep: dict) -> str:
    """The digest page's Inventory sweep section."""
    rows = []
    for a in sweep.get("apps") or []:
        state = a.get("unmeasured") or "measured"
        rows.append(f'<tr><th scope="row">{report._esc(a["target"])}</th><td>{report._esc(state)}</td><td>{report._esc(a.get("e2e"))} · tree {report._esc(a.get("e2e_tree") or "unchecked")}</td>'
                    f'<td>{_n(a.get("surfaces"))}</td><td>{_n(a.get("never_walked"))}</td><td>{_n(a.get("not_walked"))}</td>'
                    f'<td>{a.get("screens", 0)}</td><td>{report._esc(a.get("repeat"))}</td>'
                    f'<td>{a.get("classified", 0)} / {a.get("unclassified", 0)}</td></tr>')
    table = ('<div class="tbl"><table><thead><tr><th>App</th><th>Run</th><th>E2E</th><th>Map surfaces</th><th>Never walked</th>'
             '<th>NOT_WALKED overlays</th><th>Inventory screens</th><th>Repeat</th><th>Classified / left</th></tr></thead><tbody>'
             + "".join(rows) + "</tbody></table></div>")
    s = sweep.get("summary") or {}
    parts = []
    for key, title in (("by_role", "By text role"), ("by_action", "By action"), ("bespoke", "Bespoke controls"), ("choices", "By choice")):
        items = s.get(key) or []
        body = "".join(f'<li><code class="rid">{report._esc(i["id"])}</code> {report._esc(i["group"])} — {i["renderings"]} renderings, '
                       f'divergent: {report._esc(", ".join(i["divergent"]))}</li>' for i in items)
        parts.append(f"<h3>{report._esc(title)}</h3>" + (f'<ul class="plain">{body}</ul>' if body else '<p class="muted">None.</p>'))
    per = s.get("per_app") or {}
    per_rows = "".join(f'<tr><th scope="row">{report._esc(app)}</th>' + "".join(f"<td>{c.get(cls, 0)}</td>" for cls in inventory.CLASSES)
                       + f'<td>{c.get("total", 0)}</td></tr>' for app, c in per.items())
    per_table = ('<div class="tbl"><table><thead><tr><th>App</th>' + "".join(f"<th>{cls}</th>" for cls in inventory.CLASSES)
                 + "<th>Total</th></tr></thead><tbody>" + per_rows + "</tbody></table></div>")
    return report._section("inventory-sweep", f"Inventory sweep ({report._esc(sweep.get('phase'))})", table + "".join(parts) + per_table,
                           "e2e capture on each app's own throwaway instance; unknown is a state, never 0")
