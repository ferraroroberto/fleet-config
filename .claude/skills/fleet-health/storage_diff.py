"""Storage-bloat and Docker flags for one /fleet-health run.

The hub's diagnostics capture already measures storage (``run.storage`` in each
machine's export: drives, known growers, top folders, Docker state). This helper
measures nothing: it reads that section from this run's per-machine exports,
diffs it against the newest earlier run that has one, and prints the flags.
Read-only -- it never prunes, deletes or stops anything, and writes nothing.

A part the capture could not establish is reported ``unknown (<why>)``, never
as zero and never as a pass: an unmeasured size is not diffed, a Docker engine
that does not answer is its own flag, and a missing section is ``unknown``.

Output (``analyse``)::

    STORAGE|<machine>|status=compared|previous=<date>|flags=<n>|unknown=<n>
    STORAGE|<machine>|status=baseline|flags=<n>|unknown=<n>   (no earlier section)
    STORAGE|<machine>|status=unknown|reason=<why>             (no section at all)
    FLAG|<machine>|<kind>|<detail>[|hint=<safe cleanup>]
    UNKNOWN|<machine>|<field>|<reason>

followed by a markdown block to paste under each ledger entry's Findings.

Thresholds are generic defaults, overridable per run by environment variable;
no machine-specific value lives here.

Stdlib only. Exit codes: 0 = ok · 2 = bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Pin stdout/stderr to UTF-8 at the entry point: under captured stdout on
# Windows a non-cp1252 character in a hub-supplied reason would exit 1.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

GIB = 1024 ** 3
DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A machine without Docker installed is a fact about the machine, not a Docker
# engine that stopped answering; it is still reported unknown, just not flagged.
DOCKER_ABSENT_MARK = "docker CLI not on PATH"

# Label fragment -> the safe cleanup to name beside a growth flag. Text only:
# this skill reports, a human decides.
HINTS = (
    ("onedrive", "weekly OneDrive log prune (fleet-config#2172)"),
    ("docker", "Docker's WSL disk never shrinks by itself: prune unused images "
               "and volumes inside Docker, then compact the virtual disk"),
)


@dataclass(frozen=True)
class Thresholds:
    free_pct: float = 10.0
    free_bytes: int = 50 * GIB
    growth_pct: float = 10.0
    growth_bytes: int = 5 * GIB

    @classmethod
    def from_env(cls) -> "Thresholds":
        def num(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, default))
            except ValueError:
                return default

        return cls(
            free_pct=num("FLEET_HEALTH_STORAGE_FREE_PCT", cls.free_pct),
            free_bytes=int(num("FLEET_HEALTH_STORAGE_FREE_GB", cls.free_bytes / GIB) * GIB),
            growth_pct=num("FLEET_HEALTH_STORAGE_GROWTH_PCT", cls.growth_pct),
            growth_bytes=int(num("FLEET_HEALTH_STORAGE_GROWTH_GB", cls.growth_bytes / GIB) * GIB),
        )


@dataclass(frozen=True)
class Finding:
    """One flag (``flag=True``) or one unknown (``flag=False``)."""

    flag: bool
    kind: str
    detail: str
    hint: str = ""


def fmt_bytes(n: float) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def hint_for(label: str) -> str:
    low = label.lower()
    return next((hint for frag, hint in HINTS if frag in low), "")


def reason_of(item: dict) -> str:
    return str(item.get("reason") or item.get("status") or "unknown")


# ----------------------------------------------------------------- sizes


def entries(storage: dict) -> dict[tuple[str, str], dict]:
    """Every known grower (by label) and top folder (by path) in one section."""
    found: dict[tuple[str, str], dict] = {}
    growers = storage.get("growers") or {}
    for item in growers.get("items") or []:
        if item.get("label"):
            found[("grower", str(item["label"]))] = item
    top = storage.get("top_folders") or {}
    for root in top.get("roots") or []:
        for folder in root.get("folders") or []:
            if folder.get("path"):
                found[("folder", str(folder["path"]))] = folder
    return found


def exceeds(delta: float, base: float, th: Thresholds) -> bool:
    """Growth worth a flag: over both the byte floor and the percentage."""
    if delta <= th.growth_bytes:
        return False
    return base <= 0 or delta / base * 100 > th.growth_pct


def growth_finding(kind: str, key: str, cur: dict, prev: Optional[dict],
                   th: Thresholds) -> Optional[Finding]:
    status = cur.get("status")
    if status == "absent":
        return None
    label = f"{kind} {key}"
    hint = hint_for(key) if kind == "grower" else ""
    prev_status = prev.get("status") if prev else "absent"
    prev_bytes = int(prev["bytes"]) if prev and prev_status == "ok" else (0 if prev_status == "absent" else None)

    if status == "ok":
        size = int(cur["bytes"])
        if prev_bytes is None:
            return Finding(False, "growth-unknown",
                           f"{label}: previous size unknown ({reason_of(prev or {})})")
        if prev is None and kind == "grower":
            return None  # a newly configured grower has no past to diff against
        if exceeds(size - prev_bytes, prev_bytes, th):
            word = "new" if prev is None else f"{fmt_bytes(prev_bytes)} -> {fmt_bytes(size)}"
            return Finding(True, f"{kind}-growth",
                           f"{label} +{fmt_bytes(size - prev_bytes)} ({word})", hint)
        return None

    # Current size not established: a lower bound can still prove growth.
    lower = cur.get("lower_bound_bytes")
    if prev_bytes is not None and lower is not None and exceeds(int(lower) - prev_bytes, prev_bytes, th):
        return Finding(True, f"{kind}-growth",
                       f"{label} grew by at least {fmt_bytes(int(lower) - prev_bytes)} "
                       f"(now unknown: {reason_of(cur)})", hint)
    if prev is None and kind == "folder":
        return None
    return Finding(False, "growth-unknown", f"{label}: size unknown ({reason_of(cur)})")


def growth_findings(cur: dict, prev: dict, th: Thresholds) -> list[Finding]:
    cur_entries, prev_entries = entries(cur), entries(prev)
    out: list[Finding] = []
    for (kind, key), item in cur_entries.items():
        found = growth_finding(kind, key, item, prev_entries.get((kind, key)), th)
        if found:
            out.append(found)
    return out


# ---------------------------------------------------------- drives, docker


def drive_findings(storage: dict, th: Thresholds) -> list[Finding]:
    drives = storage.get("drives") or {}
    if drives.get("status") != "ok":
        return [Finding(False, "drives-unknown", f"drives: {reason_of(drives)}")]
    out: list[Finding] = []
    for item in drives.get("items") or []:
        mount = str(item.get("mount") or "?")
        if item.get("status") != "ok":
            out.append(Finding(False, "drive-unknown", f"drive {mount}: {reason_of(item)}"))
            continue
        total, free = int(item.get("total_bytes") or 0), int(item.get("free_bytes") or 0)
        pct = free / total * 100 if total else 0.0
        if total and (pct < th.free_pct or free < th.free_bytes):
            out.append(Finding(True, "low-free-space",
                               f"drive {mount}: {fmt_bytes(free)} free of {fmt_bytes(total)} "
                               f"({pct:.1f}%)"))
    return out


def docker_findings(storage: dict) -> list[Finding]:
    docker = storage.get("docker")
    if not isinstance(docker, dict) or not docker:
        return [Finding(False, "docker-unknown", "docker: no section in the capture")]
    if docker.get("status") != "ok":
        reason = reason_of(docker)
        if DOCKER_ABSENT_MARK in reason:
            return [Finding(False, "docker-not-installed", f"docker: {reason}")]
        return [Finding(True, "docker-unreachable", f"docker engine did not answer: {reason}")]
    out: list[Finding] = []
    containers = docker.get("containers") or {}
    if containers.get("status") != "ok":
        out.append(Finding(False, "docker-unknown", f"docker containers: {reason_of(containers)}"))
    for item in containers.get("items") or []:
        if item.get("health") == "unhealthy":
            out.append(Finding(True, "docker-unhealthy",
                               f"container {item.get('name') or '?'} is unhealthy "
                               f"(state {item.get('state') or '?'})"))
    usage = docker.get("usage") or {}
    if usage.get("status") != "ok":
        out.append(Finding(False, "docker-unknown", f"docker usage totals: {reason_of(usage)}"))
    return out


# ------------------------------------------------------------ run layout


def load_storage(path: Path) -> Optional[dict]:
    """The export's storage section, or None when absent or unreadable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    run = payload.get("run") if isinstance(payload, dict) else None
    storage = run.get("storage") if isinstance(run, dict) else None
    return storage if isinstance(storage, dict) and storage else None


def machine_ids(out_dir: Path) -> list[str]:
    return sorted(p.name[:-len(".json")] for p in out_dir.glob("*.json")
                  if not p.name.endswith(".drift.json") and not p.name.startswith("."))


def previous_storage(runs_dir: Path, run_date: str, machine: str) -> tuple[str, Optional[dict]]:
    """The newest earlier run's storage section for this machine, if any."""
    dates = sorted((d.name for d in runs_dir.iterdir()
                    if d.is_dir() and DATE_DIR.match(d.name) and d.name < run_date), reverse=True)
    for date in dates:
        storage = load_storage(runs_dir / date / f"{machine}.json")
        if storage:
            return date, storage
    return "", None


def analyse_machine(machine: str, out_dir: Path, th: Thresholds) -> tuple[str, list[Finding]]:
    """(STORAGE line, findings) for one machine of the run in ``out_dir``."""
    storage = load_storage(out_dir / f"{machine}.json")
    if storage is None:
        reason = "no storage section in the export (older hub build or an ingested capture)"
        return f"STORAGE|{machine}|status=unknown|reason={reason}", [
            Finding(False, "storage-unknown", reason)]
    findings = drive_findings(storage, th) + docker_findings(storage)
    prev_date, prev = previous_storage(out_dir.parent, out_dir.name, machine)
    if prev is not None:
        findings += growth_findings(storage, prev, th)
    flags = sum(1 for f in findings if f.flag)
    unknown = len(findings) - flags
    head = (f"status=compared|previous={prev_date}" if prev is not None else "status=baseline")
    return f"STORAGE|{machine}|{head}|flags={flags}|unknown={unknown}", findings


def render_manifest(machine: str, line: str, findings: list[Finding]) -> list[str]:
    lines = [line]
    for f in findings:
        if f.flag:
            hint = f"|hint={f.hint}" if f.hint else ""
            lines.append(f"FLAG|{machine}|{f.kind}|{f.detail}{hint}")
        else:
            lines.append(f"UNKNOWN|{machine}|{f.kind}|{f.detail}")
    return lines


def render_markdown(machine: str, line: str, findings: list[Finding]) -> list[str]:
    status = line.split("|")[2].removeprefix("status=")
    flags = [f for f in findings if f.flag]
    unknown = [f for f in findings if not f.flag]
    head = {"baseline": "first storage section, no earlier run to diff against",
            "unknown": "storage not measured"}.get(status, "diffed against the previous run")
    out = [f"**Storage** — {machine}: {head}; {len(flags)} flag(s), {len(unknown)} unknown"]
    for f in flags:
        out.append(f"- FLAG {f.kind}: {f.detail}" + (f" — safe cleanup: {f.hint}" if f.hint else ""))
    for f in unknown:
        out.append(f"- unknown ({f.kind}): {f.detail}")
    return out


def cmd_analyse(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    if not out_dir.is_dir() or not DATE_DIR.match(out_dir.name):
        print(f"--out-dir must be an existing dated run directory, got {out_dir}", file=sys.stderr)
        return 2
    th = Thresholds.from_env()
    machines = machine_ids(out_dir)
    manifest: list[str] = []
    markdown: list[str] = []
    for machine in machines:
        line, findings = analyse_machine(machine, out_dir, th)
        manifest += render_manifest(machine, line, findings)
        markdown += render_markdown(machine, line, findings) + [""]
    print(f"RUN_DATE={out_dir.name}")
    print(f"MACHINES={len(machines)}")
    print("\n".join(manifest))
    print("\n--- MARKDOWN ---")
    print("\n".join(markdown).rstrip())
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fleet-health storage and Docker flags")
    sub = parser.add_subparsers(dest="cmd", required=True)
    analyse = sub.add_parser("analyse", help="flag storage growth and Docker problems")
    analyse.add_argument("--out-dir", required=True,
                         help="this run's dated directory (OUT_DIR= from capture.py)")
    args = parser.parse_args(argv)
    return cmd_analyse(args)


if __name__ == "__main__":
    sys.exit(main())
