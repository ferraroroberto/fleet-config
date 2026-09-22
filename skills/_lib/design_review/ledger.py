"""Stage 5 of /design-review — the run ledger and the run-to-run diff (fleet-config#974).

Every run leaves a few-KB entry in a **local state file**,
`<hooks state>/design-review/<target>/ledger.json` — never an issue
comment: filing is opt-in, the report must open with a diff even when
nothing was ever filed, and a state file ports to fleet-config-lite. The
entry carries rule ids and statuses, grades and version stamps; never a
screenshot path, captured page text, an evidence item or a judge's prose.

Entry (the last `LEDGER_KEEP` are kept, oldest dropped first):

    {"run_id":                  "<UTC stamp — the run directory's name>",
     "generated_at":            "<evaluate.json generated_at>",
     "commit":                  "<target checkout HEAD, from the metrics envelope>",
     "live_build":              "<git_sha the live app reports on api_version_path>" | null,
     "rubric_version":          "<evaluate.json rubric_version>",
     "judgment_rubric_version": "<judgment.rubric_version>" | null,
     "overall":                 {"score": float, "grade": str},
     "categories":              {"<category>": "<grade>"},
     "rules":                   {"<rule id>": "pass" | "fail" | "unmeasured"},
     "uncatalogued":            [{"question", "title_norm", "severity", "owner"}]}   # judgment status ok only

`live_build` is best-effort (one GET of `base_url + api_version_path`,
`null` when unreadable) and is **never folded into `commit`**: `commit` is
what the target checkout says, `live_build` what the running process
says, and the two differ exactly when the tray is serving an older build.

Diff, by rule id, against the previous entry:

    {"previous_run":   "<run_id>" | null,          # null: first recorded run
     "fixed":          [id],   # fail -> pass
     "regressed":      [id],   # pass -> fail
     "new":            [id],   # fails now, id absent from the previous entry
     "unchanged":      [id],   # same status (a rule first seen passing lands here: nothing to act on)
     "unmeasured":     [id],   # unmeasured on either side — never fixed, never regressed
     "rubric_changed": {"from": str, "to": str} | null}

An `unmeasured` category is never a regression: a rule unmeasured on
either side goes to `unmeasured[]` whatever the other side said.

Public surface:

    ledger_path(target)                        -> Path
    load(target)                               -> [entry]
    entry_from_doc(doc, run_id, live_build)    -> entry
    record(run_dir, doc=None, live_build=None) -> entry     (appends / replaces same run_id, trims, atomic write)
    previous(target, before_run_id)            -> entry | None
    diff(current_doc, previous_entry)          -> diff_doc
    live_build(base_url, api_version_path)     -> str | None

stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hooks_state  # noqa: E402
import service_probe  # noqa: E402

from .judgment import STATUS_OK, normalise_title  # noqa: E402

LEDGER_KEEP = 20
LEDGER_NAME = "ledger.json"
STATUSES = ("pass", "fail", "unmeasured")


def ledger_path(target: str) -> Path:
    return hooks_state.state_dir() / "design-review" / str(target) / LEDGER_NAME


def load(target: str) -> List[dict]:
    """The recorded entries, oldest first; a missing or unreadable file is `[]`."""
    path = ledger_path(target)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return [e for e in entries if isinstance(e, dict) and e.get("run_id")] if isinstance(entries, list) else []


def live_build(base_url: Optional[str], api_version_path: Optional[str]) -> Optional[str]:
    """The `git_sha` the running app reports, or None — never raises, never a verdict."""
    if not base_url or not api_version_path:
        return None
    port = urlsplit(str(base_url)).port
    if not port:
        return None
    return service_probe.running_sha(int(port), str(api_version_path))


def entry_from_doc(doc: dict, run_id: str, live: Optional[str] = None) -> dict:
    """One ledger entry from an evaluate document. Ids, statuses, grades, stamps — nothing captured."""
    j = doc.get("judgment") if isinstance(doc.get("judgment"), dict) else None
    unc: List[dict] = []
    if j and j.get("status") == STATUS_OK:
        for u in j.get("uncatalogued") or []:
            if isinstance(u, dict):
                unc.append({"question": u.get("question"), "title_norm": normalise_title(u.get("title")),
                            "severity": u.get("severity"), "owner": u.get("owner")})
    overall = doc.get("overall") or {}
    return {
        "run_id": str(run_id),
        "generated_at": doc.get("generated_at"),
        "commit": doc.get("commit"),
        "live_build": live,
        "rubric_version": doc.get("rubric_version"),
        "judgment_rubric_version": (j.get("rubric_version") if j else None),
        "overall": {"score": overall.get("score"), "grade": overall.get("grade")},
        "categories": {str(c): v.get("grade") for c, v in (doc.get("categories") or {}).items() if isinstance(v, dict)},
        "rules": {str(r.get("id")): (r.get("status") if r.get("status") in STATUSES else "unmeasured")
                  for r in doc.get("rules") or [] if isinstance(r, dict) and r.get("id")},
        "uncatalogued": unc,
    }


def run_id_of(run_dir: Path, doc: Optional[dict] = None) -> str:
    """The run directory's name — the UTC stamp `capture.run_dir_for` chose."""
    if doc and doc.get("run_dir"):
        return Path(str(doc["run_dir"])).name
    return Path(run_dir).name


def _write(path: Path, entries: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    hooks_state.sweep_stale_atomic_temps(path)
    fd, tmp = tempfile.mkstemp(prefix=hooks_state.atomic_tmp_prefix(path), suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"schema_version": 1, "keep": LEDGER_KEEP, "entries": entries}, fh, indent=1, ensure_ascii=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def record(run_dir: Path, doc: Optional[dict] = None, live: Optional[str] = None) -> dict:
    """Append this run's entry to its target's ledger (replacing an entry with the same run_id).

    `doc` defaults to `<run_dir>/evaluate.json`; a missing document is a
    ValueError — the ledger never records a run that was not evaluated.
    """
    run_dir = Path(run_dir)
    if doc is None:
        path = run_dir / "evaluate.json"
        if not path.is_file():
            raise ValueError(f"no evaluate.json in {run_dir}: run `evaluate` first")
        doc = json.loads(path.read_text(encoding="utf-8"))
    target = str(doc.get("target") or run_dir.parent.name)
    entry = entry_from_doc(doc, run_id_of(run_dir, doc), live)
    entries = [e for e in load(target) if e.get("run_id") != entry["run_id"]]
    entries.append(entry)
    entries.sort(key=lambda e: str(e.get("run_id")))
    _write(ledger_path(target), entries[-LEDGER_KEEP:])
    return entry


def previous(target: str, before_run_id: Optional[str] = None) -> Optional[dict]:
    """The most recent entry that precedes `before_run_id` (run ids are UTC stamps, so string order is time order).

    A `before_run_id` the ledger does not hold yet (the normal case — `diff`
    runs before `record`) compares against the latest earlier entry; a run
    older than everything recorded has nothing to compare against (None),
    never a later run.
    """
    entries = [e for e in load(target) if e.get("run_id") != before_run_id]
    if before_run_id:
        entries = [e for e in entries if str(e.get("run_id")) < str(before_run_id)]
    return entries[-1] if entries else None


def diff(current_doc: dict, previous_entry: Optional[dict]) -> dict:
    """Rule-id diff of `current_doc` against a ledger entry (None = first recorded run)."""
    prev_rules: Dict[str, str] = dict((previous_entry or {}).get("rules") or {})
    out = {"previous_run": (previous_entry or {}).get("run_id"), "fixed": [], "regressed": [], "new": [],
           "unchanged": [], "unmeasured": [], "rubric_changed": None}
    for r in current_doc.get("rules") or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        rid, cur = str(r["id"]), str(r.get("status") or "unmeasured")
        prev = prev_rules.get(rid)
        if cur == "unmeasured" or prev == "unmeasured":
            out["unmeasured"].append(rid)
        elif prev is None:
            (out["new"] if cur == "fail" else out["unchanged"]).append(rid)
        elif cur == prev:
            out["unchanged"].append(rid)
        elif prev == "fail" and cur == "pass":
            out["fixed"].append(rid)
        else:
            out["regressed"].append(rid)
    prev_v, cur_v = (previous_entry or {}).get("rubric_version"), current_doc.get("rubric_version")
    if previous_entry is not None and str(prev_v) != str(cur_v):
        out["rubric_changed"] = {"from": prev_v, "to": cur_v}
    return out


def diff_counts(d: dict) -> Dict[str, int]:
    return {k: len(d.get(k) or []) for k in ("fixed", "regressed", "new", "unchanged", "unmeasured")}
