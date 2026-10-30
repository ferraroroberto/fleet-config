"""Bind a layout-lint proof manifest to the tree it proves, and judge it at finish."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import gate_evidence
import git_run
import ux_surface
from fleet_core import fleet_toml, hooks_state
from ui_proof import store

RECORD_NAME = "ui-proof.json"
# The legs `capture_states` takes by default; a criterion's state needs all four.
REQUIRED_LEGS = (("phone", "light"), ("phone", "dark"), ("desktop", "light"), ("desktop", "dark"))
# Proof cannot be produced until the capture helper is vendored, so the gate
# only refuses where it is; elsewhere a missing proof is reported, loudly.
ENFORCING_COMPONENT = "layout_lint"
NO_RECORD = "no proof manifest recorded for this branch; "
CAPTURE_HINT = ("capture it with project-scaffolding's tests/e2e/_layout_lint.py capture_states "
                "(docs/playwright-ui-testing.md), then run `ui_proof record`")


@dataclass
class Verdict:
    """What the finish gate prints: the proof state, whether it blocks, and what is missing."""

    state: str
    enforced: bool
    touched: str
    need: List[str] = field(default_factory=list)
    detail: str = ""
    # `present|missing|not checked` for `.proof/<PR>/index.html`, and `.proof/` hygiene problems:
    # both refuse whether or not layout_lint is vendored, since neither needs the capture helper.
    page: str = "not checked"
    hygiene: List[str] = field(default_factory=list)

    @property
    def blocks(self) -> bool:
        return ((self.enforced and self.state not in ("n/a", "fresh"))
                or self.page == "missing" or bool(self.hygiene))


def file_sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def load_manifest(path: Path) -> Optional[List[dict]]:
    """The manifest's entries, or `None` when it is missing or not a manifest."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = data.get("entries") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else None


def finding_fails(finding: dict) -> bool:
    return finding.get("severity") == "error" and not finding.get("allowed")


def failing_lines(entries: List[dict]) -> List[str]:
    """Public-safe lines (kind, element, measurement; never page text) for each failing finding."""
    lines = []
    for e in entries:
        for f in e.get("findings") or []:
            if finding_fails(f):
                pair = f" x {f['other']}" if f.get("other") else ""
                lines.append(f"{e.get('state')} {e.get('viewport')}-{e.get('theme')}: "
                             f"{f.get('kind')} {f.get('element')}{pair}: {f.get('detail')}")
    return lines


def missing_legs(entries: List[dict], states: List[str]) -> List[str]:
    """`<state> <viewport>-<theme>` for every required leg the manifest lacks."""
    have = {(e.get("state"), e.get("viewport"), e.get("theme")) for e in entries}
    return [f"{s} {v}-{t}" for s in states for v, t in REQUIRED_LEGS if (s, v, t) not in have]


def parse_criteria(pairs: List[str]) -> Dict[str, List[str]]:
    """`["state=criterion text", ...]` -> `{state: [criterion, ...]}`; raises ValueError on a malformed pair."""
    criteria: Dict[str, List[str]] = {}
    for pair in pairs:
        state, sep, text = pair.partition("=")
        if not sep or not state.strip() or not text.strip():
            raise ValueError(f"--criterion needs '<state>=<criterion text>', got {pair!r}")
        criteria.setdefault(state.strip(), []).append(text.strip())
    return criteria


def criterion_lines(value: object) -> List[str]:
    """A state's criterion lines; a record written with one text per state reads as a one-line list."""
    if isinstance(value, str):
        return [value] if value else []
    return [str(v) for v in value] if isinstance(value, list) else []


def layout_lint_vendored(repo: Path) -> bool:
    """True when the repo's `.fleet.toml` `[vendored]` table lists `layout_lint`."""
    vendored = fleet_toml.table(fleet_toml.load(repo), "vendored")
    return vendored is not None and ENFORCING_COMPONENT in vendored


def _record_path(repo: Path) -> Optional[Path]:
    return gate_evidence.git_path(repo, RECORD_NAME)


def load_record(repo: Path) -> Optional[dict]:
    path = _record_path(repo)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def record(repo: Path, manifest: Path, criteria: Dict[str, List[str]]) -> Tuple[bool, str]:
    """Bind `manifest` + `criteria` to the current tree; `(ok, PROOF=... line)`."""
    if not criteria:
        return False, "PROOF=not_recorded reason=no --criterion given; map each UI criterion to its state"
    entries = load_manifest(manifest)
    if entries is None:
        return False, f"PROOF=not_recorded reason=no readable manifest at {manifest}"
    wtree, why = gate_evidence.tree_hash(repo)
    path = _record_path(repo)
    if wtree is None or path is None:
        return False, f"PROOF=not_recorded reason={why or 'not a git working tree'}"
    data = {"manifest": str(manifest.resolve()), "manifest_sha256": file_sha256(manifest),
            "wtree": wtree, "criteria": criteria,
            "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    hooks_state.atomic_write_text(path, json.dumps(data, indent=2))
    gaps = missing_legs(entries, list(criteria))
    errors = failing_lines(entries)
    return True, (f"PROOF=recorded states={','.join(criteria)} legs={len(entries)} "
                  f"missing_legs={len(gaps)} errors={len(errors)} wtree={wtree[:12]}")


def _judge_record(repo: Path, rec: Optional[dict]) -> Tuple[str, List[str], str]:
    """`(state, need, detail)` for a recorded proof against the current tree."""
    if rec is None:
        return "missing", [NO_RECORD + CAPTURE_HINT], ""
    manifest = Path(str(rec.get("manifest", "")))
    entries = load_manifest(manifest)
    if entries is None or file_sha256(manifest) != rec.get("manifest_sha256"):
        return "stale", [f"the recorded manifest {manifest} is gone or changed since `record`; "
                         "re-capture and re-record"], ""
    current, why = gate_evidence.tree_hash(repo)
    if current is None:
        return "unknown", [f"tree hash not computable: {why}"], ""
    if current != rec.get("wtree"):
        paths = gate_evidence.changed_paths(repo, str(rec.get("wtree")), current)
        return "stale", ["the tree changed since the proof was recorded ("
                         + (", ".join(paths) or "unknown paths") + "); re-capture and re-record"], ""
    criteria = rec.get("criteria") or {}
    gaps = missing_legs(entries, list(criteria))
    if gaps:
        return "missing", ["missing captured states: " + ", ".join(gaps)], ""
    errors = failing_lines(entries)
    if errors:
        return "failing", ["layout lint errors (fix, or allow with a reason): " + "; ".join(errors)], ""
    return "fresh", [], f"states={','.join(criteria)} legs={len(entries)} errors=0 recorded={rec.get('utc')}"


def evaluate(repo: Path, base: Optional[str] = None, pr: Optional[int] = None) -> Verdict:
    """The finish gate's verdict for `repo`'s branch diff.

    With `pr`, `.proof/<PR>/index.html` is required and is itself the proof: a repo with no recorded manifest
    is judged by the built page and the lint findings its captures carry (lint "not run" when it has none).
    """
    verdict = _evaluate_manifest(repo, base)
    if verdict.touched == "no":
        return verdict
    verdict.hygiene = store.guard_lines(repo)
    if pr is not None:
        if load_record(repo) is None:
            verdict.need = [n for n in verdict.need if not n.startswith(NO_RECORD)]
        folder = store.pr_folder(repo, pr)
        verdict.page = "present" if folder else "missing"
        if folder is None:
            verdict.need.append(f"no .proof/{pr}/index.html; `ui_proof capture-before`/`capture-after`, "
                                f"then `ui_proof build . --pr {pr}`")
        elif load_record(repo) is None:
            _judge_page(verdict, folder)
    verdict.need += verdict.hygiene
    return verdict


def _judge_page(verdict: Verdict, folder: Path) -> None:
    """Judge a built page with no recorded manifest: the lint findings its captures carry decide."""
    after = [r for r in store.load_data(folder).get("after", []) if "findings" in r]
    errors = failing_lines(after)
    if errors:
        verdict.state = "failing"
        verdict.need.append("layout lint errors (fix, or allow with a reason): " + "; ".join(errors))
        return
    lint = f"clean on {len(after)} leg(s)" if after else "not run (no layout_lint manifest)"
    notes = [*verdict.need, f"page built; layout lint {lint}"]
    if verdict.detail:
        notes.append(verdict.detail)
    verdict.state, verdict.need, verdict.detail = "fresh", [], "; ".join(notes)


def _evaluate_manifest(repo: Path, base: Optional[str]) -> Verdict:
    """The layout-lint manifest's verdict for the branch diff."""
    block = ux_surface._load_block(repo)
    if not (block and block["spec_applies"]):
        return Verdict("n/a", False, "no", detail="no design-gated UX surface (SPEC_APPLIES=no)")
    enforced = layout_lint_vendored(repo)
    changed = git_run.changed_files(repo, base or ux_surface._default_base(repo))
    if changed is None:
        touched, matched = "unknown", []
    else:
        matched = ux_surface.ui_paths(changed, block["paths"])  # type: ignore[arg-type]
        touched = "yes" if matched else "no"
    if touched == "no":
        return Verdict("n/a", enforced, "no", detail="no UX surface touched")
    state, need, detail = _judge_record(repo, load_record(repo))
    if touched == "unknown":
        views = ",".join(block["key_views"]) or "(none declared)"  # type: ignore[arg-type]
        note = f"diff could not be taken (TOUCHED=unknown), so proof is required for every key view: {views}"
        if state == "fresh":
            detail = f"{note}; {detail}"
        else:
            need = [note, *need]
    elif not detail:
        detail = "touched: " + ",".join(matched[:10]) + (f" (+{len(matched) - 10} more)" if len(matched) > 10 else "")
    return Verdict(state, enforced, touched, need, detail)
