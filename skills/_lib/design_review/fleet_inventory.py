"""`/design-review fleet inventory`: the fleet inventory sweep, in two legs.

**Capture** (`run_inventory_capture`, CLI `fleet --inventory`) is `fleet.run_fleet`'s serial loop with every app
measured by `review_app(..., e2e=True)`: the app's synthetic walk plus its own e2e suite in capture mode, one app
and one browser at a time, never the live app. An app with no synthetic launcher is `unmeasured`, never walked
live and never counted clean. Beside the dry-run digest it writes:

    sweep.json           {"schema_version", "stamp", "apps": [{"target", "root", "row", "sweep_row"}]}
    classify-<app>.md    the reader prompt, for each app with unclassified controls (`classify.py`)

and records each app's inventory hash in `<state>/design-review/_fleet/inventory-hashes.json` for the next
sweep's repeat check (`inventory_sweep.repeat_state`). The capture never files.

**Report** (`run_inventory_report`, CLI `fleet-report <fleet_dir> [--file]`) measures nothing. It reloads each
captured run's `evaluate.json` and `metrics.json`, validates each `classify-<app>.json` the readers wrote (a
dropped answer is reported, a missing file leaves that app's entries unclassified), re-runs the comparison with
the valid answers and rebuilds the digest and every body in the same fleet directory. Filing is opt-in, through
the same merge as `fleet --file`, and in both legs the app bodies also carry the surface-map lines
(SURF-01/02, `surface_ids.py`).

stdlib only; spawns nothing itself — `capture.measure_target` owns the walk and the suite, the skill owns the readers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import hooks_state, runtime_files  # noqa: E402

from . import classify, filing, fleet, inventory, inventory_sweep  # noqa: E402
from .rubric import Rubric  # noqa: E402

MANIFEST = "sweep.json"
# Each app's last inventory hash and commit across sweeps: the repeat check reads it.
HASHES = "inventory-hashes.json"
NOT_IN_SWEEP = "NOT_IN_SWEEP"


def _run_inventory(target: str, run_dir: object) -> Optional[dict]:
    """The inventory of exactly this run (not the newest run on disk), or None."""
    path = Path(str(run_dir)) / "metrics.json"
    metrics = _read_json(path)
    return inventory.app_inventory(target, metrics, path.parent.name, str(path)) if metrics else None


def hashes_path() -> Path:
    return hooks_state.state_dir() / "design-review" / "_fleet" / HASHES


def _read_json(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _save_json(path: Path, doc: dict) -> None:
    runtime_files.make_dirs(path.parent, parents=True, exist_ok=True)
    runtime_files.save_text(path, json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True), encoding="utf-8")


def _sweep_section(phase: str, rows: List[dict], dig: dict, classification: Dict[str, dict]) -> dict:
    """The digest's `inventory_sweep` section; the per-app unclassified / classified counts come from the comparison."""
    section = dig.get("inventory") or {}
    left: Dict[str, int] = {}
    for u in section.get("unclassified") or []:
        left[u["app"]] = left.get(u["app"], 0) + int(u["n"])
    answered = {a["target"]: int(a.get("classified") or 0) for a in section.get("apps") or []}
    for row in rows:
        row["unclassified"] = left.get(row["target"], 0)
        row["classified"] = answered.get(row["target"], 0)
    return {"phase": phase, "apps": rows, "classification": classification, "summary": inventory_sweep.summary(dig.get("inventory"))}


def run_inventory_capture(rb: Rubric, specs: Dict[str, Dict[str, str]], devices: List[str], projects_toml: Optional[Path] = None,
                          out_dir: Optional[Path] = None, reviewer: Optional[fleet.Reviewer] = None,
                          fetch: Optional[filing.Fetch] = None, today: Optional[str] = None,
                          catalog: Optional[dict] = None, **review_kw: object) -> dict:
    """The capture leg (module docstring). `reviewer` (tests) replaces the e2e `review_app`. Never files."""
    out_dir = out_dir or fleet.fleet_dir()
    collected: Dict[str, dict] = {}

    def review(name: str, table: dict) -> dict:
        run = reviewer or (lambda n, t: fleet.review_app(n, t, rb, specs, devices, projects_toml, e2e=True, **review_kw))  # type: ignore[arg-type]
        r = run(name, table)
        collected[r["row"]["target"]] = r
        return r

    def loader(t: str) -> Optional[dict]:
        return _run_inventory(t, collected[t]["row"]["run_dir"]) if t in collected else None

    def decorate(dig: dict) -> None:
        hashes = _read_json(hashes_path())
        rows: List[dict] = []
        classification: Dict[str, dict] = {}
        for a in dig["apps"]:
            t, r = a["target"], collected[a["target"]]
            row = inventory_sweep.app_row(a, r["doc"], loader(t))
            prev = hashes.get(t) if isinstance(hashes.get(t), dict) else None
            row["repeat"] = inventory_sweep.repeat_state(prev, row["commit"], row["inventory_hash"])
            row["previous_hash"] = (prev or {}).get("hash")
            if row["inventory_hash"]:
                hashes[t] = {"commit": row["commit"], "hash": row["inventory_hash"], "stamp": dig["stamp"]}
            rows.append(row)
            entries = classify.entries_for(dig.get("inventory"), t)
            if entries:
                run_dir = Path(str(r["row"]["run_dir"]))
                shots = classify.screenshots_of(_read_json(run_dir / "metrics.json"), run_dir)
                prompt_path, answers_path = out_dir / f"classify-{t}.md", out_dir / f"classify-{t}.json"
                runtime_files.save_text(prompt_path, classify.prompt(t, entries, shots, str(answers_path)), encoding="utf-8")
                classification[t] = {"entries": len(entries), "prompt": str(prompt_path), "answers": str(answers_path),
                                     "answered": 0, "problems": []}
        dig["inventory_sweep"] = _sweep_section("capture", rows, dig, classification)
        _save_json(hashes_path(), hashes)
        by_target = {row["target"]: row for row in rows}
        _save_json(out_dir / MANIFEST, {
            "schema_version": 1, "stamp": dig["stamp"],
            "apps": [{"target": t, "root": str(r["root"]) if r.get("root") else None, "row": r["row"],
                      "sweep_row": by_target[t]} for t, r in sorted(collected.items())]})

    return fleet.run_fleet(rb, specs, devices, projects_toml, file_issues=False, out_dir=out_dir, reviewer=review, fetch=fetch,
                           today=today, inventory_loader=loader, catalog=catalog, surface_lines=True, decorate=decorate, **review_kw)


def _saved_review(name: str, saved: Dict[str, dict]) -> dict:
    """A captured app's `{row, doc, root}` from disk, or an `unmeasured` stand-in that files nothing."""
    a = saved.get(name)
    doc = _read_json(Path(str(a["row"]["run_dir"])) / "evaluate.json") if a and a["row"].get("run_dir") else {}
    if a and doc:
        return {"row": dict(a["row"]), "doc": doc, "root": Path(a["root"]) if a.get("root") else None}
    why = "not in this sweep" if not a else "its evaluate.json is gone"
    row = {"target": name, "root": None, "run_id": None, "run_dir": None, "probe": "not run", "probe_detail": why,
           "unmeasured": NOT_IN_SWEEP, "commit": None, "live_build": None, "overall": None, "failed": [],
           "unmeasured_rules": 0, "diff": {}, "previous_run": None, "filed": [], "accepted": [], "problems": []}
    return {"row": row, "doc": {"target": name, "rules": [], "unmeasured": {"reason": NOT_IN_SWEEP, "detail": why}}, "root": None}


def _answers(out_dir: Path, targets: List[str], bare: Optional[dict]) -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """`(classified, classification)`: every valid answer by entry id, and per app what its answers file did."""
    classified: Dict[str, dict] = {}
    classification: Dict[str, dict] = {}
    for t in sorted(targets):
        entries = classify.entries_for(bare, t)
        if not entries:
            continue
        answers_path = out_dir / f"classify-{t}.json"
        rec = {"entries": len(entries), "prompt": str(out_dir / f"classify-{t}.md"), "answers": str(answers_path),
               "answered": 0, "problems": ["no answers file: every entry stays unclassified"]}
        if answers_path.is_file():
            got, problems = classify.validate(answers_path.read_text(encoding="utf-8"), t, entries)
            classified.update(got)
            rec.update(answered=len(got), problems=problems)
        classification[t] = rec
    return classified, classification


def run_inventory_report(rb: Rubric, specs: Dict[str, Dict[str, str]], out_dir: Path, projects_toml: Optional[Path] = None,
                         file_issues: bool = False, fetch: Optional[filing.Fetch] = None, upsert: Optional[filing.Upsert] = None,
                         today: Optional[str] = None, catalog: Optional[dict] = None, scaffold: object = None) -> dict:
    """The report leg (module docstring). Raises ValueError when `out_dir` holds no capture manifest."""
    out_dir = Path(out_dir)
    manifest = _read_json(out_dir / MANIFEST)
    if not manifest.get("apps"):
        raise ValueError(f"no {MANIFEST} with apps in {out_dir}: run `fleet --inventory` first")
    saved = {a["target"]: a for a in manifest["apps"]}

    def loader(t: str) -> Optional[dict]:
        return _run_inventory(t, saved[t]["row"]["run_dir"]) if t in saved and saved[t]["row"].get("run_dir") else None

    bare = fleet.compare_inventories(sorted(saved), specs, loader, catalog, scaffold)
    classified, classification = _answers(out_dir, sorted(saved), bare)

    def decorate(dig: dict) -> None:
        rows = [dict(saved[a["target"]]["sweep_row"]) if a["target"] in saved else inventory_sweep.app_row(a, {}, None)
                for a in dig["apps"]]
        dig["inventory_sweep"] = _sweep_section("report", rows, dig, classification)

    return fleet.run_fleet(rb, specs, [], projects_toml, file_issues=file_issues, out_dir=out_dir,
                           reviewer=lambda n, t: _saved_review(n, saved), fetch=fetch, upsert=upsert, today=today,
                           inventory_loader=loader, catalog=catalog, classified=classified, surface_lines=True,
                           decorate=decorate, scaffold=scaffold)
