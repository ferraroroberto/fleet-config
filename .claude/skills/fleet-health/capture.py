"""Fleet hardware-health capture driver (fleet-config#397).

Discovers every machine from the local hub's own inventory, classifies each by
*probed capability* (never by name), starts a diagnostics capture on every
machine that can take one, polls them all to completion, and writes the raw
export + generated markdown report per machine into a dated run directory.

Machine discovery is dynamic: ids come from ``/admin/api/machines/status``, so a
new box enrolled in the hub appears here with zero edits to this file. Nothing
below matches on a machine id — routing is decided by what a machine *answers*.

Captures are strictly local to each hub (``local-llm-hub`` ``docs/diagnostics.md``:
"each host's hub owns its own sampler"), so a peer is driven through its own
``/admin`` endpoint. The inventory payload carries no LAN address, so peer
addresses are resolved from the hub's ``config/models.yaml`` ``hosts:`` block,
keyed by the ids the inventory already returned.

Stdlib only (urllib) — same reason as ``hooks/slack_notify.py``: this must run
without a venv on any host.

Three verbs, because a tool call is capped well under a capture's length and the
chunk boundary has to be visible to the caller (fleet-config#314 — a scheduled
headless run that backgrounds work and ends its turn exits 0 having done
nothing)::

    start     classify every machine, start every capture, persist run state
    poll      block for one bounded chunk, print progress, print DONE=yes|no
    collect   stop stragglers, fetch artefacts, emit the final manifest

Exit codes: 0 = ok · 2 = no run state (called out of order) ·
3 = inventory unreachable · 4 = no machine could be captured.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

HUB = os.environ.get("FLEET_HEALTH_HUB", "http://127.0.0.1:8000")
HUB_PORT = int(os.environ.get("FLEET_HEALTH_HUB_PORT", "8000"))
MODELS_YAML = Path(os.environ.get(
    "FLEET_HEALTH_MODELS_YAML", r"E:/automation/local-llm-hub/config/models.yaml"))

# A weekly unattended run: one hour at 30 s ticks = 120 samples. Long enough to
# catch the idle-resident picture that matters (what is *always* loaded), short
# enough that the polling loop stays a handful of blocking chunks.
DEFAULT_DURATION_S = float(os.environ.get("FLEET_HEALTH_DURATION_S", "3600"))
DEFAULT_INTERVAL_S = float(os.environ.get("FLEET_HEALTH_INTERVAL_S", "30"))

# Never end a turn waiting on background work (fleet-config#314): the caller
# polls in blocking chunks no longer than this, so each tool call returns well
# inside its own timeout.
POLL_CHUNK_S = 540.0
POLL_EVERY_S = 15.0

TIMEOUT_S = 10.0
REPORT_TIMEOUT_S = 60.0


# ---------------------------------------------------------------- http


def _get(url: str, timeout: float = TIMEOUT_S) -> tuple[int, bytes]:
    """GET a URL. Returns (status, body); never raises on an HTTP error code."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError, socket.timeout) as exc:
        return 0, str(exc).encode("utf-8", "replace")


def _post(url: str, payload: Optional[dict] = None,
          timeout: float = TIMEOUT_S) -> tuple[int, bytes]:
    """POST JSON. The hub only parses a body when Content-Type is set."""
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError, socket.timeout) as exc:
        return 0, str(exc).encode("utf-8", "replace")


def _json(body: bytes) -> dict:
    try:
        parsed = json.loads(body.decode("utf-8", "replace") or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


# ---------------------------------------------------------------- discovery


def load_addresses() -> dict[str, str]:
    """Map machine id -> LAN address from the hub's ``hosts:`` block.

    Deliberately a tiny hand-rolled scan rather than a yaml dependency: this is
    stdlib-only, and the two fields needed (a host id key, its ``address``) sit
    at fixed indents. Unknown ids simply resolve to no address, which the caller
    reports as "not covered" rather than guessing a hostname.
    """
    if not MODELS_YAML.is_file():
        return {}
    addresses: dict[str, str] = {}
    current: Optional[str] = None
    in_hosts = False
    try:
        lines = MODELS_YAML.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        if re.match(r"^hosts:\s*$", line):
            in_hosts = True
            continue
        if in_hosts and re.match(r"^\S", line):
            break  # dedented out of the hosts block
        if not in_hosts:
            continue
        host = re.match(r"^  ([A-Za-z0-9._-]+):\s*$", line)
        if host:
            current = host.group(1)
            continue
        addr = re.match(r"^\s+address:\s*([^\s#]+)", line)
        if addr and current:
            addresses[current] = addr.group(1).strip().strip('"\'')
    return addresses


def discover() -> list[dict]:
    """Every enrolled machine, straight from the hub inventory."""
    status, body = _get(f"{HUB}/admin/api/machines/status")
    if status != 200:
        return []
    machines = _json(body).get("machines")
    return machines if isinstance(machines, list) else []


def diagnostics_base(machine: dict, addresses: dict[str, str]) -> Optional[str]:
    """The ``/admin`` base to drive this machine's own hub, or None.

    Routing is by capability only: the host we run on is reachable on loopback;
    any peer is reachable at its declared LAN address. Whether that hub actually
    *serves* diagnostics is decided by probing, not assumed here.
    """
    if machine.get("is_host"):
        return HUB
    address = addresses.get(str(machine.get("id") or ""))
    return f"http://{address}:{HUB_PORT}" if address else None


def classify(machine: dict, addresses: dict[str, str]) -> tuple[str, str, Optional[str]]:
    """Return (status, reason, diagnostics_base).

    status is ``ready`` when a capture can be started, otherwise a
    not-covered status carrying a human reason. Every machine gets a verdict —
    silence is never an option (fleet-config#397 acceptance).
    """
    mid = str(machine.get("id") or "?")
    if machine.get("dormant"):
        return "dormant", "machine is dormant — not powered for scheduled capture", None

    state = str(machine.get("state") or "")
    if state not in ("self", "up") or machine.get("reachable") is False:
        return "unreachable", f"machine did not answer the hub probe (state={state or 'unknown'})", None

    base = diagnostics_base(machine, addresses)
    if base is None:
        return ("no-address",
                "no LAN address declared for this machine, so its own hub cannot be dialled", None)

    status, _ = _get(f"{base}/admin/api/diagnostics/status", timeout=TIMEOUT_S)
    if status == 200:
        return "ready", "", base
    if status == 404:
        return ("no-diagnostics",
                f"hub at {base} answers but does not serve the diagnostics API (404) — "
                f"the peer is on an older build and needs a host sync", base)
    if status == 0:
        ssh = bool((machine.get("actions") or {}).get("ssh_terminal"))
        hint = (" — SSH-reachable, so it needs the portable sampler from "
                "local-llm-hub#316") if ssh else ""
        return "no-hub", f"no hub answering at {base}{hint}", base
    return "probe-failed", f"hub at {base} answered {status} on the diagnostics probe", base


# ---------------------------------------------------------------- capture


def start(base: str, duration_s: float, interval_s: float) -> tuple[bool, str]:
    """Start a capture. A 409 means a run is already in flight — adopt it."""
    status, body = _post(f"{base}/admin/api/diagnostics/start",
                         {"interval_s": interval_s, "duration_s": duration_s})
    if status == 200:
        run_id = str((_json(body).get("active") or {}).get("run_id") or "")
        return (True, run_id) if run_id else (False, "start returned no run_id")
    if status == 409:
        # "a capture is already running" is busy, not broken — attach to it.
        st, sbody = _get(f"{base}/admin/api/diagnostics/status")
        if st == 200:
            run_id = str((_json(sbody).get("active") or {}).get("run_id") or "")
            if run_id:
                return True, run_id
        return False, "a capture is already running but its run_id was not readable"
    detail = _json(body).get("detail") or body.decode("utf-8", "replace")[:200]
    return False, f"start failed ({status}): {detail}"


def poll_once(base: str) -> tuple[bool, int, str]:
    """(still_capturing, samples_written, last_error)."""
    status, body = _get(f"{base}/admin/api/diagnostics/status")
    if status != 200:
        return False, 0, f"status probe failed ({status})"
    payload = _json(body)
    active = payload.get("active") or {}
    return (bool(payload.get("capturing")),
            int(active.get("samples_written") or 0),
            str(active.get("last_error") or ""))


def poll_chunk(targets: dict[str, str], chunk_s: float) -> dict[str, dict]:
    """Block up to ``chunk_s`` polling every target; report per-machine progress.

    Synchronous by construction. A scheduled headless run that backgrounds this
    and ends its turn exits 0 having done nothing (fleet-config#314).
    """
    deadline = time.monotonic() + chunk_s
    progress: dict[str, dict] = {
        mid: {"capturing": True, "samples": 0, "error": ""} for mid in targets}
    while time.monotonic() < deadline:
        if not any(p["capturing"] for p in progress.values()):
            break
        for mid, base in targets.items():
            if not progress[mid]["capturing"]:
                continue
            capturing, samples, err = poll_once(base)
            # A finished run reports active=null, so samples_written reads 0.
            # Keep the high-water mark or the tail of every run looks empty.
            progress[mid] = {
                "capturing": capturing,
                "samples": max(samples, progress[mid]["samples"]),
                "error": err or progress[mid]["error"],
            }
            print(f"  · {mid}: samples={progress[mid]['samples']} "
                  f"capturing={capturing}", file=sys.stderr, flush=True)
        if not any(p["capturing"] for p in progress.values()):
            break
        time.sleep(POLL_EVERY_S)
    return progress


# ---------------------------------------------------------------- artefacts


def fetch_artefacts(base: str, run_id: str, out_dir: Path, mid: str) -> dict[str, str]:
    """Evaluate the run, then save its markdown report, json export, and drift."""
    files: dict[str, str] = {}
    # Re-evaluate so the verdict reflects the rules file as it stands today.
    _post(f"{base}/admin/api/diagnostics/runs/{run_id}/evaluate",
          timeout=REPORT_TIMEOUT_S)

    status, body = _get(f"{base}/admin/api/diagnostics/runs/{run_id}/report",
                        timeout=REPORT_TIMEOUT_S)
    if status == 200:
        path = out_dir / f"{mid}.md"
        path.write_bytes(body)
        files["report"] = str(path)

    status, body = _get(f"{base}/admin/api/diagnostics/runs/{run_id}/export",
                        timeout=REPORT_TIMEOUT_S)
    if status == 200:
        path = out_dir / f"{mid}.json"
        path.write_bytes(body)
        files["export"] = str(path)

    status, body = _get(f"{base}/admin/api/diagnostics/runs/{run_id}/drift",
                        timeout=REPORT_TIMEOUT_S)
    if status == 200:
        path = out_dir / f"{mid}.drift.json"
        path.write_bytes(body)
        files["drift"] = str(path)

    return files


def run_summary(base: str, run_id: str) -> tuple[str, int]:
    """(verdict level, sample count) for a finished run."""
    status, body = _get(f"{base}/admin/api/diagnostics/runs/{run_id}",
                        timeout=REPORT_TIMEOUT_S)
    if status != 200:
        return "unknown", 0
    payload = _json(body)
    verdict = str((payload.get("verdict") or {}).get("level") or "unknown")
    samples = int((payload.get("run") or {}).get("sample_count") or 0)
    return verdict, samples


# ---------------------------------------------------------------- main


def emit(mid: str, status: str, reason: str = "", **extra: Any) -> None:
    """One manifest line per machine — the stdout handoff the SKILL.md reads."""
    parts = [f"MACHINE={mid}", f"status={status}"]
    for key, value in extra.items():
        if value:
            parts.append(f"{key}={value}")
    if reason:
        parts.append(f"reason={reason}")
    print("|".join(parts), flush=True)


STATE_NAME = ".run-state.json"


def state_path(out_dir: Path) -> Path:
    return out_dir / STATE_NAME


def load_state(out_dir: Path) -> dict:
    path = state_path(out_dir)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def save_state(out_dir: Path, state: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path(out_dir).write_text(json.dumps(state, indent=2), encoding="utf-8")


def print_header(root: Path, out_dir: Path, today: str) -> None:
    print(f"LEDGER_ROOT={root}")
    print(f"LEDGER={root / 'fleet-health.md'}")
    print(f"OUT_DIR={out_dir}")
    print(f"RUN_DATE={today}")


def resolve_dirs(args) -> tuple[Path, Path, str]:
    today = args.date or _dt.date.today().isoformat()
    root = Path(args.ledger_root or (Path.home() / ".claude" / "fleet-health"))
    out_dir = Path(args.out_dir or (root / "runs" / today))
    return root, out_dir, today


def cmd_start(args) -> int:
    """Classify every machine, start every capture, persist the run state."""
    root, out_dir, today = resolve_dirs(args)

    machines = discover()
    if not machines:
        print(f"inventory unreachable at {HUB}/admin/api/machines/status", file=sys.stderr)
        return 3
    addresses = load_addresses()

    print_header(root, out_dir, today)
    print(f"MACHINE_COUNT={len(machines)}")

    targets: dict[str, str] = {}
    skipped: list[dict] = []
    for machine in machines:
        mid = str(machine.get("id") or "?")
        status, reason, base = classify(machine, addresses)
        if status == "ready" and base:
            targets[mid] = base
        else:
            skipped.append({"id": mid, "detail": status, "reason": reason})
            emit(mid, "not-covered", reason, detail=status)

    if args.discover_only:
        for mid, base in targets.items():
            emit(mid, "ready", base=base)
        return 0 if targets else 4

    runs: dict[str, str] = {}
    for mid, base in list(targets.items()):
        ok, result = start(base, args.duration_s, args.interval_s)
        if ok:
            runs[mid] = result
            emit(mid, "started", "", run_id=result, base=base)
        else:
            targets.pop(mid, None)
            skipped.append({"id": mid, "detail": "start-failed", "reason": result})
            emit(mid, "not-covered", result, detail="start-failed")

    save_state(out_dir, {
        "run_date": today,
        "ledger_root": str(root),
        "out_dir": str(out_dir),
        "duration_s": args.duration_s,
        "interval_s": args.interval_s,
        "targets": targets,
        "runs": runs,
        "skipped": skipped,
    })

    print(f"STARTED={len(runs)}")
    print(f"NOT_COVERED={len(skipped)}")
    if not runs:
        print("no machine could be captured this run", file=sys.stderr)
        return 4
    # Tell the caller roughly how many poll calls to expect.
    chunks = max(1, int(args.duration_s // POLL_CHUNK_S) + 1)
    print(f"POLL_CHUNKS_EXPECTED={chunks}")
    print("DONE=no")
    return 0


def cmd_poll(args) -> int:
    """Block for one bounded chunk, then return so the turn stays alive.

    Deliberately one chunk per invocation: a tool call is capped well under an
    hour, so the skill polls by calling this repeatedly. Each call is fully
    synchronous — nothing is ever left running in the background.
    """
    _root, out_dir, _today = resolve_dirs(args)
    state = load_state(out_dir)
    if not state:
        print(f"no run state at {state_path(out_dir)} — run `start` first", file=sys.stderr)
        return 2

    targets: dict[str, str] = state.get("targets") or {}
    runs: dict[str, str] = state.get("runs") or {}
    pending = {mid: targets[mid] for mid in runs if mid in targets}
    if not pending:
        print("DONE=yes")
        return 0

    progress = poll_chunk(pending, min(args.chunk_s, POLL_CHUNK_S))

    # Carry the sample high-water across poll calls. A machine that finished in
    # an earlier chunk reports active=null (samples 0) forever after, which
    # would otherwise read as a failed capture in the scheduled job's log.
    seen: dict[str, int] = dict(state.get("samples") or {})
    for mid, info in progress.items():
        seen[mid] = max(int(info["samples"]), int(seen.get(mid, 0)))
        emit(mid, "progress", info["error"], samples=seen[mid],
             capturing=str(bool(info["capturing"])).lower())
    state["samples"] = seen
    save_state(out_dir, state)

    still = [mid for mid, info in progress.items() if info["capturing"]]
    print(f"DONE={'no' if still else 'yes'}")
    if still:
        print(f"STILL_CAPTURING={','.join(still)}")
    return 0


def cmd_collect(args) -> int:
    """Stop anything still running, fetch every artefact, emit the manifest."""
    root, out_dir, today = resolve_dirs(args)
    state = load_state(out_dir)
    if not state:
        print(f"no run state at {state_path(out_dir)} — run `start` first", file=sys.stderr)
        return 2

    targets: dict[str, str] = state.get("targets") or {}
    runs: dict[str, str] = state.get("runs") or {}
    skipped: list[dict] = state.get("skipped") or []

    print_header(root, out_dir, today)
    for entry in skipped:
        emit(str(entry.get("id") or "?"), "not-covered",
             str(entry.get("reason") or ""), detail=str(entry.get("detail") or ""))

    captured = 0
    for mid, run_id in runs.items():
        base = targets.get(mid)
        if not base:
            continue
        capturing, _, _ = poll_once(base)
        if capturing:
            # Past its deadline and still going: stop it so the run is readable.
            _post(f"{base}/admin/api/diagnostics/stop")
        files = fetch_artefacts(base, run_id, out_dir, mid)
        if not files:
            emit(mid, "not-covered", "capture completed but no artefacts could be fetched",
                 detail="artefacts-missing", run_id=run_id)
            continue
        captured += 1
        verdict, samples = run_summary(base, run_id)
        emit(mid, "captured", "", run_id=run_id, verdict=verdict, samples=samples,
             report=files.get("report", ""), export=files.get("export", ""),
             drift=files.get("drift", ""))

    print(f"CAPTURED={captured}")
    print(f"NOT_COVERED={len(skipped)}")
    return 0 if captured else 4


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fleet hardware-health capture driver")
    parser.add_argument("--out-dir", default=None,
                        help="run directory (default: a dated dir under the ledger root)")
    parser.add_argument("--ledger-root", default=None,
                        help="ledger root (default ~/.claude/fleet-health)")
    parser.add_argument("--date", default=None,
                        help="run date YYYY-MM-DD (default today) — must match across calls")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start_cmd = sub.add_parser("start", help="classify machines and start every capture")
    start_cmd.add_argument("--duration-s", type=float, default=DEFAULT_DURATION_S,
                           help="capture length per machine (default %(default)s)")
    start_cmd.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S,
                           help="sample interval (default %(default)s)")
    start_cmd.add_argument("--discover-only", action="store_true",
                           help="classify machines and exit without capturing")

    poll_cmd = sub.add_parser("poll", help="block for one bounded chunk, then return")
    poll_cmd.add_argument("--chunk-s", type=float, default=POLL_CHUNK_S,
                          help="max seconds to block (capped at %(default)s)")

    sub.add_parser("collect", help="fetch artefacts and emit the final manifest")

    args = parser.parse_args(argv)
    if args.cmd == "start":
        return cmd_start(args)
    if args.cmd == "poll":
        return cmd_poll(args)
    return cmd_collect(args)


if __name__ == "__main__":
    sys.exit(main())
