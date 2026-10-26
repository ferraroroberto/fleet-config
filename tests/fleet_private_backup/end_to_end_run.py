import json
import sqlite3

from _suite import _cfg, _git, _write, check, tmp
from latest_and_notes import toml_path
from runtime_data import live_db, rd_src, writer
import backup as bp


# ---- end-to-end run ----------------------------------------------------
e2e_cfg = _cfg(tmp, dest=tmp / "e2e-dest", transcripts_src=tmp / "e2e-transcripts",
               transcripts_dest=tmp / "e2e-tdest")
_write(e2e_cfg.transcripts_src / "proj" / "session.jsonl", '{"a":1}')

original_preflight = bp.cli._preflight
bp.cli._preflight = lambda source, dest: None  # temp dirs share a volume by construction
try:
    code, manifests = bp.run(e2e_cfg, only=None, toml_path=toml_path, notify=False,
                             today="2026-08-11")
    check(code == bp.EXIT_OK, f"e2e: a clean run exits 0, got {code}")
    check({m["leg"] for m in manifests} == {"repos", "transcripts"},
          "e2e: the two git-derived legs ran, and the runtime-data leg is "
          "cleanly absent because its root does not exist (fleet-config#724)")
    repos_manifest = next(m for m in manifests if m["leg"] == "repos")
    check(repos_manifest["status"] == "ok", "e2e: the repos leg reports ok")
    check("demo-repo" in repos_manifest["groups"], "e2e: the demo repo was snapshotted")
    snapshot = e2e_cfg.dest / "2026-08-11"
    check((snapshot / "demo-repo" / ".env").is_file(),
          "e2e: .env landed in the dated snapshot")
    check((snapshot / "demo-repo" / "identity" / "me.md").is_file(),
          "e2e: the personal directory landed in the dated snapshot")
    check(not (snapshot / "demo-repo" / ".venv").exists(),
          "e2e: .venv is absent from the written snapshot")
    check((e2e_cfg.dest / bp.LATEST_DIR / "demo-repo" / ".env").is_file(),
          "e2e: latest/ mirrors the snapshot")
    check((snapshot / bp.MANIFEST_NAME).is_file(), "e2e: the manifest was written")
    written = json.loads((snapshot / bp.MANIFEST_NAME).read_text(encoding="utf-8"))
    check(written["verification"]["status"] == "pass",
          "e2e: the run verified its own output")
    check(written["policy"]["bulk_dir_mb"] > 0, "e2e: the manifest records the policy")
    transcripts_manifest = next(m for m in manifests if m["leg"] == "transcripts")
    check(transcripts_manifest["totals"]["files"] == 1,
          "e2e: the transcripts leg snapshotted its tree")
    check((e2e_cfg.transcripts_dest / "2026-08-11" / "projects" / "proj"
           / "session.jsonl").is_file(), "e2e: the transcripts leg wrote to its own dest")

    code2, manifests2 = bp.run(e2e_cfg, only=None, toml_path=toml_path, notify=False,
                               today="2026-08-12")
    repos2 = next(m for m in manifests2 if m["leg"] == "repos")
    check(code2 == bp.EXIT_OK, f"e2e: the second run also exits 0, got {code2}")
    check(repos2["totals"]["linked"] > 0 and repos2["totals"]["copied"] == 0,
          f"e2e: an unchanged second run is all hardlinks, got {repos2['totals']}")

    dry_code, dry_manifests = bp.run(e2e_cfg, dry_run=True, toml_path=toml_path,
                                     notify=False, today="2026-08-13")
    check(dry_code == bp.EXIT_OK and all(m["status"] == "dry-run" for m in dry_manifests),
          "e2e: --dry-run reports without writing")
    check(not (e2e_cfg.dest / "2026-08-13").exists(),
          "e2e: --dry-run created no snapshot directory")
    check(not (tmp / "runtime-data-dest").exists(),
          "e2e: a skipped runtime-data leg writes nothing at all, not an empty snapshot")

    # ---- end-to-end, runtime-data leg ---------------------------------
    rd_e2e = _cfg(tmp, dest=tmp / "rd-e2e-dest",
                  transcripts_src=tmp / "rd-e2e-transcripts",
                  transcripts_dest=tmp / "rd-e2e-tdest",
                  runtime_data_src=rd_src, runtime_data_dest=tmp / "rd-e2e-rdest")
    _write(rd_e2e.transcripts_src / "proj" / "session.jsonl", '{"a":1}')
    rd_code, rd_manifests = bp.run(rd_e2e, toml_path=toml_path, notify=False,
                                   today="2026-08-20")
    check(rd_code == bp.EXIT_OK, f"rd-e2e: a clean three-leg run exits 0, got {rd_code}")
    check({m["leg"] for m in rd_manifests} ==
          {"repos", "transcripts", bp.RUNTIME_DATA_LEG},
          f"rd-e2e: all three legs ran, got {[m['leg'] for m in rd_manifests]}")
    rd_manifest = next(m for m in rd_manifests if m["leg"] == bp.RUNTIME_DATA_LEG)
    check(rd_manifest["status"] == "ok", f"rd-e2e: the leg reports ok, got {rd_manifest}")
    check(rd_manifest["policy"]["max_file_mb"] is None,
          "rd-e2e: the written manifest records that no size cap was applied")
    rd_snapshot = (rd_e2e.runtime_data_dest / "2026-08-20" /
                   bp.RUNTIME_DATA_GROUP / "home-automation")
    check((rd_snapshot / "telemetry.sqlite3").is_file(),
          "rd-e2e: the database landed in the dated snapshot")
    check(not (rd_snapshot / "telemetry.sqlite3-wal").exists()
          and not (rd_snapshot / "telemetry.sqlite3-shm").exists(),
          "rd-e2e: no -wal/-shm sidecar was written beside it")
    snap_rows = None
    if (rd_snapshot / "telemetry.sqlite3").is_file():
        snap_conn = sqlite3.connect(str(rd_snapshot / "telemetry.sqlite3"))
        try:
            snap_rows = snap_conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        except sqlite3.Error as exc:
            snap_rows = f"unreadable: {exc}"
        finally:
            snap_conn.close()
    check(snap_rows == 50,
          f"rd-e2e: the SNAPSHOTTED file on the backup volume opens and holds the "
          f"live rows, got {snap_rows}")
    check(rd_manifest["verification"]["status"] == "pass",
          "rd-e2e: the leg verified its own output like any other")
    check((rd_e2e.runtime_data_dest / bp.RESTORE_NOTE_NAME).is_file(),
          "rd-e2e: the destination carries its own restore note")

    # A database that goes bad is a failure AND, once it is the only one,
    # a zero-file regression — the protection that catches a leg quietly
    # backing up nothing where it used to back something up.
    writer.close()  # release the live handle before overwriting the file
    live_db.write_text("no longer a database at all", encoding="utf-8")
    for sidecar in ("telemetry.sqlite3-wal", "telemetry.sqlite3-shm"):
        (live_db.parent / sidecar).unlink(missing_ok=True)
    for stray in ("notes.txt", "big.blob", "notes-wal"):
        (live_db.parent / stray).unlink(missing_ok=True)
    bad_code, bad_manifests = bp.run(rd_e2e, toml_path=toml_path, notify=False,
                                     today="2026-08-21")
    bad_manifest = next(m for m in bad_manifests if m["leg"] == bp.RUNTIME_DATA_LEG)
    check(bad_manifest["groups"][bp.RUNTIME_DATA_GROUP]["status"] == "failed",
          "rd-e2e: an unsnapshottable database fails its group, never a quiet pass")
    check(any("telemetry.sqlite3" in e
              for e in bad_manifest["groups"][bp.RUNTIME_DATA_GROUP]["errors"]),
          "rd-e2e: the manifest names the database it could not snapshot")
    check(bad_manifest["regressions"] == [bp.RUNTIME_DATA_GROUP],
          f"rd-e2e: backing up 0 files where there were files still trips the "
          f"zero-file regression check, got {bad_manifest['regressions']}")
    check(bad_code != bp.EXIT_OK, f"rd-e2e: the run exits non-zero, got {bad_code}")
finally:
    bp.cli._preflight = original_preflight
    writer.close()

# ---- latest/ incremental reconcile (fleet-config#721) ------------------
# A full rebuild used to re-hardlink every file in the tree on every run,
# regardless of how much actually changed. This proves a run over a small
# delta touches only the delta: one file changed, one removed, one added.
incr_fleet = tmp / "incr-fleet"
incr_repo = incr_fleet / "incr-repo"
incr_repo.mkdir(parents=True)
_git(incr_repo, "init", "-q")
_write(incr_repo / ".gitignore", "priv/\n")
_write(incr_repo / "priv" / "keep.md", "unchanged")
_write(incr_repo / "priv" / "change.md", "v1")
_write(incr_repo / "priv" / "remove.md", "gone soon")

incr_cfg = _cfg(tmp, source_root=incr_fleet, dest=tmp / "incr-dest",
                transcripts_src=tmp / "incr-transcripts", transcripts_dest=tmp / "incr-tdest")
original_preflight3 = bp.cli._preflight
bp.cli._preflight = lambda source, dest: None  # temp dirs share a volume by construction
try:
    code1, manifests1 = bp.run(incr_cfg, only="incr-repo", toml_path=toml_path,
                               notify=False, today="2026-08-11")
    check(code1 == bp.EXIT_OK, f"incr: the baseline run is clean, got {code1}")
    repos1 = next(m for m in manifests1 if m["leg"] == "repos")
    check(repos1.get("latest_mode") == "full",
          f"incr: the first run has no previous manifest to diff against, so it "
          f"fully rebuilds latest/, got {repos1.get('latest_mode')}")
    latest_root = incr_cfg.dest / bp.LATEST_DIR / "incr-repo" / "priv"
    keep_ino_before = (latest_root / "keep.md").stat().st_ino

    # A small delta: one file changes content, one is removed, one is added.
    _write(incr_repo / "priv" / "change.md", "v2")
    (incr_repo / "priv" / "remove.md").unlink()
    _write(incr_repo / "priv" / "new.md", "brand new")

    code2, manifests2 = bp.run(incr_cfg, only="incr-repo", toml_path=toml_path,
                               notify=False, today="2026-08-12")
    check(code2 == bp.EXIT_OK, f"incr: the delta run is clean, got {code2}")
    repos2 = next(m for m in manifests2 if m["leg"] == "repos")
    check(repos2.get("latest_mode") == "incremental",
          f"incr: a run with a valid previous manifest reconciles instead of "
          f"rebuilding, got {repos2.get('latest_mode')}")
    delta = repos2.get("latest_delta") or {}
    check(delta == {"added": 1, "updated": 1, "removed": 1},
          f"incr: the work done is proportional to the change (1 add, 1 update, "
          f"1 remove out of 4 files), not to the tree size, got {delta}")

    check((latest_root / "new.md").is_file()
          and (latest_root / "new.md").read_text(encoding="utf-8") == "brand new",
          "incr: latest/ picks up the added file")
    check((latest_root / "change.md").read_text(encoding="utf-8") == "v2",
          "incr: latest/ reflects the changed file's new content")
    check(not (latest_root / "remove.md").exists(),
          "incr: a file deleted from source is gone from latest/ too")
    keep_ino_after = (latest_root / "keep.md").stat().st_ino
    check(keep_ino_before == keep_ino_after,
          "incr: an unchanged file is never touched — same inode before and after")
    latest_manifest_written = json.loads(
        (incr_cfg.dest / bp.LATEST_DIR / bp.MANIFEST_NAME).read_text(encoding="utf-8"))
    check(latest_manifest_written.get("date") == "2026-08-12",
          "incr: latest/manifest.json — outside every group, so untouched by the "
          "per-group diff — is still refreshed to today's, not left stale")

    # Full rebuild remains reachable as a repair path, and lands on the same
    # membership the incremental reconcile just produced (fleet-config#721 AC).
    bp.rebuild_latest(incr_cfg.dest, incr_cfg.dest / "2026-08-12")
    latest_rels = {
        str(p.relative_to(incr_cfg.dest / bp.LATEST_DIR)).replace("\\", "/")
        for p in (incr_cfg.dest / bp.LATEST_DIR).rglob("*") if p.is_file()
    }
    snapshot_rels = {
        str(p.relative_to(incr_cfg.dest / "2026-08-12")).replace("\\", "/")
        for p in (incr_cfg.dest / "2026-08-12").rglob("*") if p.is_file()
    }
    check(latest_rels == snapshot_rels,
          "incr: a full rebuild is byte-identical in membership to what the "
          "incremental reconcile had already produced")

    # Self-heal: a file the manifest says is unchanged, but that has gone
    # missing from latest/ (a killed reconcile, manual meddling), must not
    # be silently trusted — the next reconcile notices and relinks it.
    (latest_root / "keep.md").unlink()
    code3, manifests3 = bp.run(incr_cfg, only="incr-repo", toml_path=toml_path,
                               notify=False, today="2026-08-13")
    repos3 = next(m for m in manifests3 if m["leg"] == "repos")
    check(repos3.get("latest_mode") == "incremental",
          f"incr: an untouched-content day still reconciles incrementally, "
          f"got {repos3.get('latest_mode')}")
    check((latest_root / "keep.md").is_file()
          and (latest_root / "keep.md").read_text(encoding="utf-8") == "unchanged",
          "incr: a file missing from latest/ despite an unchanged manifest entry "
          "is relinked, not left absent")

    code4, manifests4 = bp.run(incr_cfg, only="incr-repo", toml_path=toml_path,
                               notify=False, today="2026-08-14", rebuild_latest_full=True)
    repos4 = next(m for m in manifests4 if m["leg"] == "repos")
    check(repos4.get("latest_mode") == "full",
          f"incr: --rebuild-latest-full forces the repair path even with a valid "
          f"previous manifest, got {repos4.get('latest_mode')}")
finally:
    bp.cli._preflight = original_preflight3
