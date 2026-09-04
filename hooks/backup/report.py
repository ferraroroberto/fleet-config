"""Human-readable run report (logged per leg) and the summary ping.

Split out of `hooks/backup_private.py` (fleet-config#731). Works purely off
already-built manifest dicts, so its only sibling dependency is `config.py`
(for `EXIT_OK`).
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _lib  # noqa: E402
import notify_send  # noqa: E402

from .config import EXIT_OK

logger = logging.getLogger("backup_private")


def _mb(value: int) -> str:
    return f"{value / 1024 / 1024:.1f} MB"


def _leg_duration_seconds(manifest: Dict[str, Any]) -> Optional[float]:
    """Wall time from `started_at` to `finished_at`, or `None` if either is absent.

    Logged per leg (rather than only the overall run total `main()` already
    prints) so the transcripts leg's contribution to total runtime stays
    visible night over night — the trend that walked fleet-config#720 into a
    watchdog kill in the first place (fleet-config#721).
    """
    started, finished = manifest.get("started_at"), manifest.get("finished_at")
    if not started or not finished:
        return None
    try:
        return (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return None


def report(manifest: Dict[str, Any]) -> None:
    totals = manifest["totals"]
    duration = _leg_duration_seconds(manifest)
    logger.info(
        "ℹ️ %s: %d files, %s (%d linked, %d copied) across %d groups%s",
        manifest["leg"], totals["files"], _mb(totals["bytes"]),
        totals["linked"], totals["copied"], totals["groups"],
        f" in {duration:.1f}s" if duration is not None else "",
    )
    latest_mode = manifest.get("latest_mode")
    if latest_mode == "incremental":
        delta = manifest.get("latest_delta") or {}
        logger.info(
            "   🔗 %s: latest/ reconciled incrementally (+%d ~%d -%d) instead of a "
            "full %d-file rebuild",
            manifest["leg"], delta.get("added", 0), delta.get("updated", 0),
            delta.get("removed", 0), manifest.get("latest_files", 0),
        )
    elif latest_mode == "full":
        logger.info(
            "   🔗 %s: latest/ fully rebuilt (%d files)",
            manifest["leg"], manifest.get("latest_files", 0),
        )
    recovered = manifest.get("recovered_marker")
    if recovered:
        logger.info(
            "   ⚠️ %s: recovered an unfinished run's marker for %s (pid %s, started %s) "
            "— that run never reached manifest.json; today's run overwrote it",
            manifest["leg"], manifest["date"], recovered.get("pid"), recovered.get("started_at"),
        )
    for name in manifest.get("skipped_interrupted") or []:
        logger.info(
            "   ⚠️ %s: %s never finished (marker present) — skipped as a hardlink source",
            manifest["leg"], name,
        )
    for name, payload in sorted(manifest["groups"].items()):
        for excluded in payload["bulk_excluded"]:
            logger.info(
                "   ⚠️ %s/%s excluded by the bulk-dir guard (%.1f MB, %d files) "
                "— add backup_include to keep it",
                name, excluded["path"], excluded["mb"], excluded["files"],
            )
        if payload.get("vanished"):
            logger.info(
                "   ℹ️ %s: %d source file(s) vanished mid-run (a live app rotating "
                "its own artifacts) — not a failure",
                name, len(payload["vanished"]),
            )
        for error in payload["errors"]:
            logger.error("   ❌ %s: %s", name, error)
    verification = manifest.get("verification") or {}
    if verification:
        logger.info("   🔎 verification: %s (%d sampled)",
                    verification.get("status"), verification.get("sampled", 0))
    for group in manifest.get("regressions") or []:
        logger.error("   ❌ %s backed up 0 files but had files last run", group)
    if manifest.get("pruned"):
        logger.info("   🧹 pruned %d old snapshots: %s",
                    len(manifest["pruned"]), ", ".join(manifest["pruned"]))


def _notify_summary(manifests: Sequence[Dict[str, Any]], exit_code: int) -> str:
    """One ASCII-only line per leg (fleet-config#507: a Windows command line is
    not a UTF-8-safe channel, and this text also reaches the chat via argv)."""
    icon = "OK" if exit_code == EXIT_OK else "FAILED"
    parts = []
    for manifest in manifests:
        totals = manifest["totals"]
        parts.append(
            f"{manifest['leg']}: {totals['files']} files | "
            f"{totals['bytes'] / 1024 / 1024:.0f} MB | "
            f"{totals['bulk_excluded_dirs']} bulk dirs skipped"
        )
    # A leg that failed preflight never produced a manifest. Saying so beats a
    # ping that trails off after the dash and reads like a truncated message.
    detail = " ; ".join(parts) if parts else f"no leg completed (exit {exit_code})"
    return f"Fleet private backup {icon} - {detail}"


def _notify(manifests: Sequence[Dict[str, Any]], exit_code: int) -> None:
    category = "log" if exit_code == EXIT_OK else "attention"
    chat, _ = _lib.resolve_notify_target(Path.cwd(), category=category)
    if not chat:
        return
    prefix = "✅" if exit_code == EXIT_OK else "❌"
    notify_send.notify(f"{prefix} {_notify_summary(manifests, exit_code)}", chat)

