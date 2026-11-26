"""One Board ask per suggestion, posted through `chief_ops.py plan ask`.

Each ask gets a stable id derived from the report and suggestion ids, so a
re-run finds it. A suggestion is skipped when its ask is still open on the
plan or the ledger shows it was asked before (an answered ask leaves the
plan, so the plan alone cannot stop a repeat). Posting goes through the
plan CLI, not the plan module, so the journal records each ask like any other.
"""

from __future__ import annotations

import enum
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import chief_plan
from fleet_core.state_rows import iso_z, state_file, utc_now, write_rows
from no_window import NO_WINDOW

from proposal_report.model import Quadrant, Report, Suggestion

LEDGER_FILENAME = "proposal-asks-ledger.json"
CHIEF_OPS = Path(__file__).resolve().parent.parent / "chief_ops.py"
ACCEPT, DECLINE, LATER = "Accept", "Decline", "Later"
# Quick wins are worth taking and money pits worth dropping; the other two are the owner's call.
_RECOMMENDED = {Quadrant.QUICK_WIN: ACCEPT, Quadrant.MONEY_PIT: DECLINE}
# A Board call through the plan CLI is a local file write; a minute means it is stuck on the lock.
POST_TIMEOUT_SECONDS = 60


class AskState(enum.StrEnum):
    POSTED = "posted"
    DRY_RUN = "dry-run"
    OPEN = "already-open"      # the ask is still waiting on the plan
    ASKED = "already-asked"    # the ledger shows it was posted before
    FAILED = "failed"


@dataclass
class Ask:
    ask_id: str
    item: Suggestion
    question: str
    detail: str
    options: List[str]
    recommended: Optional[str]
    state: AskState = AskState.DRY_RUN
    reason: str = ""

    def argv(self) -> List[str]:
        args = ["ask", self.question, "--repo", self.item.repo, "--detail", self.detail, "--id", self.ask_id]
        for option in self.options:
            args += ["--option", option]
        if self.recommended:
            args += ["--recommended", self.recommended]
        return args


@dataclass
class Outcome:
    asks: List[Ask] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(ask.state == AskState.FAILED for ask in self.asks)


def ask_id(report_id: str, item_id: str) -> str:
    """Stable and inside the plan's id rule (a letter, then at most 31 of [a-z0-9-])."""
    digest = hashlib.sha1(f"{report_id}:{item_id}".encode("utf-8")).hexdigest()[:12]
    return f"pr-{digest}"


def check_page_url(url: str) -> str:
    """The page link must open on a phone, so only an absolute http(s) URL without a fragment is taken."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.fragment:
        raise ValueError(f"--page-url must be an absolute http(s) URL with no #fragment, got {url!r}")
    return url


def build(report: Report, page_url: str) -> List[Ask]:
    asks = []
    for item in report.suggestions:
        link = f"{page_url}#{item.anchor}"
        tail = (f" Impact {item.impact}/5, effort {item.effort}/5 ({item.quadrant.value}), "
                f"repo {item.repo}. Page: {link}")
        room = chief_plan.MAX_PROSE - len(tail)
        summary = item.summary if len(item.summary) <= room else item.summary[:room - 1].rstrip() + "…"
        asks.append(Ask(
            ask_id=ask_id(report.id, item.id),
            item=item,
            question=f"Take up \"{item.title}\"?"[:chief_plan.MAX_PROSE],
            detail=summary + tail,
            options=[f"{ACCEPT}::file it as an issue in {item.repo}"[:chief_plan.MAX_PROSE],
                     f"{DECLINE}::drop it", f"{LATER}::keep it for a later round"],
            recommended=_RECOMMENDED.get(item.quadrant),
        ))
    return asks


def read_ledger(path: Path) -> Dict[str, Any]:
    """The asked-before ledger; missing is empty, unreadable refuses rather than risk repeats."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: ledger is not valid JSON ({exc}); fix or remove it by hand") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path}: ledger must be a JSON object")
    return data


def _open_ids(plan_path: Path) -> set:
    doc = chief_plan.load(plan_path)
    return {row.get("id") for row in doc["waiting_on_roberto"] if row.get("id")}


def _post(ask: Ask, plan_path: Optional[Path]) -> None:
    cmd = [sys.executable, str(CHIEF_OPS), "plan"]
    if plan_path is not None:
        cmd += ["--plan-file", str(plan_path)]
    try:
        proc = subprocess.run(cmd + ask.argv(), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=POST_TIMEOUT_SECONDS, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        ask.state, ask.reason = AskState.FAILED, f"plan ask timed out after {POST_TIMEOUT_SECONDS}s"
        return
    if proc.returncode == 0 and f"id={ask.ask_id}" in proc.stdout:
        ask.state = AskState.POSTED
        return
    ask.state = AskState.FAILED
    ask.reason = " ".join((proc.stderr or proc.stdout).split())[:300] or f"exit {proc.returncode}"


def run(report: Report, page_url: str, *, dry_run: bool, plan_path: Optional[Path] = None,
        ledger_path: Optional[Path] = None) -> Outcome:
    """Post (or, dry, list) one ask per suggestion; already-asked ones are skipped either way."""
    ledger_path = ledger_path or state_file(LEDGER_FILENAME)
    ledger = read_ledger(ledger_path)
    open_ids = _open_ids(plan_path or chief_plan.plan_file())
    outcome = Outcome(build(report, check_page_url(page_url)))
    for ask in outcome.asks:
        if ask.ask_id in open_ids:
            ask.state = AskState.OPEN
        elif ask.ask_id in ledger:
            ask.state = AskState.ASKED
        elif dry_run:
            ask.state = AskState.DRY_RUN
        else:
            _post(ask, plan_path)
            if ask.state == AskState.POSTED:
                ledger[ask.ask_id] = {"report": report.id, "item": ask.item.id, "page": page_url,
                                      "asked_at": iso_z(utc_now())}
                write_rows(ledger_path, ledger)
    return outcome
