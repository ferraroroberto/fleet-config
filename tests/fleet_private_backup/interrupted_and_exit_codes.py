import dataclasses
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

from _suite import DB_LOCK_WAIT_LIMIT, NOW, ROOT, _cfg, _write, check, tmp
from latest_and_notes import toml_path
import backup as bp


# ---- an interrupted run: marker left behind, recovered, and reported ---
# A real death mid-write, simulated by making write_group raise after the
# marker is already down — the same failure shape as a killed process or a
# power loss between the first write and manifest.json (fleet-config#607).
torn_cfg = _cfg(tmp, dest=tmp / "torn-dest", transcripts_src=tmp / "torn-transcripts",
                transcripts_dest=tmp / "torn-tdest")
original_preflight2 = bp.cli._preflight
original_write_group = bp.cli.write_group
bp.cli._preflight = lambda source, dest: None  # temp dirs share a volume by construction

class _SimulatedCrash(Exception):
    pass

def _crashing_write_group(*args, **kwargs):
    raise _SimulatedCrash("process died mid-write")

try:
    code1, _ = bp.run(torn_cfg, only="demo-repo", toml_path=toml_path,
                      notify=False, today="2026-08-10")
    check(code1 == bp.EXIT_OK, "torn: the baseline run is clean")
    check(not bp.has_run_marker(torn_cfg.dest / "2026-08-10"),
          "torn: an ordinary successful run removes its own marker")

    bp.cli.write_group = _crashing_write_group
    crashed = False
    try:
        bp.run(torn_cfg, only="demo-repo", toml_path=toml_path,
              notify=False, today="2026-08-11")
    except _SimulatedCrash:
        crashed = True
    finally:
        bp.cli.write_group = original_write_group
    check(crashed, "torn: the simulated mid-write crash propagates (nothing swallows it)")

    torn_snapshot = torn_cfg.dest / "2026-08-11"
    check(bp.has_run_marker(torn_snapshot),
          "torn: a run interrupted between the first write and the manifest "
          "leaves .run-in-progress behind")
    check(not (torn_snapshot / bp.MANIFEST_NAME).exists(),
          "torn: an interrupted run never reaches manifest.json")

    state, detail = bp.freshness(torn_cfg.dest, torn_cfg, now=NOW)
    check(state == bp.FRESHNESS_UNKNOWN and detail.get("reason") == "last run did not finish",
          f"torn: --check-freshness reports unknown over the torn snapshot, got {state}")

    # The next day's run skips the torn snapshot as a hardlink source and
    # names it as the interrupted predecessor in its own manifest/report.
    code3, manifests3 = bp.run(torn_cfg, only="demo-repo", toml_path=toml_path,
                               notify=False, today="2026-08-12")
    check(code3 == bp.EXIT_OK, "torn: the following day's run is clean")
    repos3 = next(m for m in manifests3 if m["leg"] == "repos")
    check(repos3["skipped_interrupted"] == ["2026-08-11"],
          f"torn: the next run's report names the interrupted predecessor, "
          f"got {repos3['skipped_interrupted']}")
    check(bp.has_run_marker(torn_snapshot),
          "torn: a stuck marker from a different date is left untouched, not silently cleared")

    report_buf = io.StringIO()
    report_handler = logging.StreamHandler(report_buf)
    prior_level = bp.logger.level
    bp.logger.addHandler(report_handler)
    bp.logger.setLevel(logging.INFO)
    try:
        bp.report(repos3)
    finally:
        bp.logger.removeHandler(report_handler)
        bp.logger.setLevel(prior_level)
    check("2026-08-11" in report_buf.getvalue() and "never finished" in report_buf.getvalue(),
          f"torn: report() prints the interrupted predecessor's name, got "
          f"{report_buf.getvalue()!r}")

    # Re-running the SAME date the torn run died on is self-healing: it
    # overwrites the directory, recovers (and reports) the stale marker, and
    # clears it on success.
    code4, manifests4 = bp.run(torn_cfg, only="demo-repo", toml_path=toml_path,
                               notify=False, today="2026-08-11")
    check(code4 == bp.EXIT_OK, "torn: a clean re-run over the same date succeeds")
    repos4 = next(m for m in manifests4 if m["leg"] == "repos")
    check(repos4["recovered_marker"] is not None
          and repos4["recovered_marker"]["leg"] == "repos",
          f"torn: the re-run's manifest names the marker it recovered, "
          f"got {repos4['recovered_marker']}")
    check(not bp.has_run_marker(torn_snapshot),
          "torn: a successful re-run over a marked directory clears the marker")

    recovered_buf = io.StringIO()
    recovered_handler = logging.StreamHandler(recovered_buf)
    prior_level2 = bp.logger.level
    bp.logger.addHandler(recovered_handler)
    bp.logger.setLevel(logging.INFO)
    try:
        bp.report(repos4)
    finally:
        bp.logger.removeHandler(recovered_handler)
        bp.logger.setLevel(prior_level2)
    check("recovered" in recovered_buf.getvalue(),
          f"torn: report() names the marker it recovered on this run, got "
          f"{recovered_buf.getvalue()!r}")
finally:
    bp.cli._preflight = original_preflight2
    bp.cli.write_group = original_write_group

# ---- the real projects.toml: [backup] must not steal [global]'s keys ---
# A TOML table header claims every bare key that follows it. Adding [backup]
# above `architecture_ignore` re-parented that list out of [global] and
# dropped four repos off the architecture map; this asserts the ordering
# rather than trusting the comment that now says so (fleet-config#590).
real_toml = bp.config._projects_toml_path()  # the hooks' own resolution: example when no local list
real_text = real_toml.read_text(encoding="utf-8")
real_data = json.loads(json.dumps(bp._read_toml(real_toml), default=str))
check("architecture_ignore" in real_data.get("global", {}),
      "projects.toml: architecture_ignore still belongs to [global]")
check("never_kill_ports" in real_data.get("global", {}),
      "projects.toml: [global] still owns never_kill_ports")
check("architecture_ignore" not in real_data.get("backup", {}),
      "projects.toml: [backup] has not swallowed a [global] key")
check(real_text.rindex("[backup]") > real_text.rindex("[global]"),
      "projects.toml: [backup] stays below [global]")
check(real_data.get("backup", {}).get("source_root"),
      "projects.toml: the real [backup] table is populated")
check(bp.load_backup_config(real_toml).bulk_dir_bytes > 0,
      "projects.toml: the real config loads")
# fleet-config#1493: life-os's .claude/ sits just under bulk_dir_mb; the real
# table must keep it past the guard however large it grows.
life_os_ov = bp.load_repo_overrides(Path("E:/automation/life-os"), real_toml)
real_cfg_tiny = dataclasses.replace(bp.load_backup_config(real_toml), bulk_dir_bytes=1)
life_os_items = [(Path("x"), ".claude/skills/a/context/c.md", 4096),
                 (Path("y"), "conversations/c.md", 4096)]
life_os_kept, life_os_dropped = bp._apply_bulk_guard(life_os_items, real_cfg_tiny, life_os_ov)
check(".claude" in life_os_ov.include
      and ".claude/skills/a/context/c.md" in {rel for _, rel, _ in life_os_kept}
      and [d["path"] for d in life_os_dropped] == ["conversations/"],
      "projects.toml: life-os keeps .claude/ past the bulk-dir guard")

# ---- the unattended log reaches stdout, line-buffered ------------------
# fleet-config#605: logging.basicConfig defaults to stderr, so the
# app-launcher Jobs pane captured 0 bytes for a 35-minute run. Driven as a
# real subprocess with the streams split, because that is the only way the
# defect shows — in-process logging assertions cannot see it.
proc = subprocess.run(
    [sys.executable, str(ROOT / "hooks" / "backup_private.py"), "--check-freshness"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    timeout=120, creationflags=bp._lib.NO_WINDOW,
)
check("BACKUP_FRESHNESS=" in proc.stdout,
      f"log: the report goes to stdout, got stdout={proc.stdout[:80]!r}")
src = (ROOT / "hooks" / "backup" / "cli.py").read_text(encoding="utf-8")
check("stream=sys.stdout" in src, "log: logging is pinned to stdout, not the stderr default")
check("line_buffering=True" in src,
      "log: stdout is line-buffered so a piped run shows progress before it exits")

# ---- a wedged run still leaves its last step in the log ----------------
# fleet-config#1418: the launcher's watchdog killed a run after an hour of
# a 0-byte log. Line buffering was already on (#605); the gap was that the
# first line was only written after every leg's selection *and* the whole
# repos leg. Reproduced here as a real wedge: a runtime-data database held
# under an exclusive lock blocks selection, and a breadcrumb naming the
# step it reached must reach the pipe while the process is still blocked.
wedge = tmp / "wedge-1418"
for name in ("fleet", "transcripts", "codex", "runtime-data-src"):
    (wedge / name).mkdir(parents=True)
_write(wedge / "transcripts" / "proj" / "s.jsonl", "{}")
locked_db = wedge / "runtime-data-src" / "locked.sqlite3"
holder = sqlite3.connect(str(locked_db), isolation_level=None)
holder.execute("CREATE TABLE t (x)")
holder.execute("BEGIN EXCLUSIVE")
wedge_toml = _write(wedge / "projects.toml", "\n".join([
    "[backup]",
    *(f'{key} = "{(wedge / sub).as_posix()}"' for key, sub in (
        ("source_root", "fleet"), ("dest", "dest"),
        ("transcripts_src", "transcripts"), ("transcripts_dest", "tdest"),
        ("codex_sessions_src", "codex"), ("codex_sessions_dest", "cdest"),
        ("runtime_data_src", "runtime-data-src"), ("runtime_data_dest", "rdest"),
    )),
    "",
]))
env = {k: v for k, v in os.environ.items() if k != "PYTHONUNBUFFERED"}
wedged = subprocess.Popen(
    [sys.executable, str(ROOT / "hooks" / "backup_private.py"), "--dry-run",
     "--no-notify", "--config", str(wedge_toml)],
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
    creationflags=bp._lib.NO_WINDOW,
)
seen: list = []

def _drain() -> None:
    for raw in wedged.stdout:
        seen.append(raw.decode("utf-8", "replace").rstrip())

reader = threading.Thread(target=_drain, daemon=True)
reader.start()
deadline = time.monotonic() + 30
while time.monotonic() < deadline and not any("locked.sqlite3" in line for line in seen):
    time.sleep(0.2)
still_running = wedged.poll() is None
try:
    check(bool(seen) and still_running,
          f"log: the first line reaches a pipe while the run is still going, got {seen!r}")
    check(still_running and any("locked.sqlite3" in line for line in seen),
          f"log: a wedged run's last line names the step it is blocked on, got {seen!r}")
    # fleet-config#1422: the lock used to hold this run forever and the
    # test could only end it with a kill. Now the database is reported as a
    # failed snapshot after the lock deadline and the run carries on to the
    # end and exits non-zero on its own.
    try:
        wedged_exit = wedged.wait(timeout=DB_LOCK_WAIT_LIMIT)
    except subprocess.TimeoutExpired:
        wedged_exit = None
    reader.join(10)
    check(wedged_exit not in (None, 0),
          f"log: a run blocked by a locked database finishes and exits non-zero, got {wedged_exit!r}")
    check(any("sqlite-snapshot" in line and "locked.sqlite3" in line for line in seen),
          f"log: ...and reports that database as a failed snapshot, got {seen!r}")
finally:
    if wedged.poll() is None:
        wedged.kill()
    wedged.wait(timeout=30)
    holder.close()

# ---- exit-code severity ordering --------------------------------------
check(bp._worst([bp.EXIT_REPO_FAILURE, bp.EXIT_VERIFY_FAILED]) == bp.EXIT_VERIFY_FAILED,
      "exit: a verification failure outranks a repo failure")
check(bp._worst([bp.EXIT_VERIFY_FAILED, bp.EXIT_DEST_UNUSABLE]) == bp.EXIT_DEST_UNUSABLE,
      "exit: an unusable destination outranks everything")
check(bp._worst([]) == bp.EXIT_OK, "exit: no problems means exit 0")
