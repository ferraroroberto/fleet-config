"""Attributable, finite atomic-write temporaries in ``hooks/state/`` (fleet-config#864).

#816 fixed ``hooks/session_state.py``; this extends the same contract to the
two other writers into the same shared directory, now both routed through one
helper per tree (``hooks/_lib.py`` and ``skills/_lib/hooks_state.py``):

* ``hooks/codex_attention.py`` -> ``codex-attention-dedup.json``
* ``skills/_lib/active_issue.py`` -> ``active-issues.json`` (and
  ``chief-managed.json``, which reuses its ``write_rows``)

A hard kill between ``mkstemp`` and ``os.replace`` runs no ``except OSError``
handler. It is simulated here by an ``os.replace`` that raises a
``BaseException`` those handlers do not catch, which strands the temp exactly
as ``taskkill /F`` does. Every directory is a ``TemporaryDirectory`` — never the
live ``~/.claude/hooks/state/``.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "_lib"))
sys.path.insert(0, str(ROOT / "hooks"))

import _lib as hooks_lib  # noqa: E402
import codex_attention  # noqa: E402
import active_issue  # noqa: E402
import hooks_state  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "session_state_under_test_864", ROOT / "hooks" / "session_state.py")
assert SPEC and SPEC.loader
session_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(session_state)

STALE_AGE_SECONDS = hooks_lib.ATOMIC_TMP_SWEEP_AFTER_SECONDS + 7200
DEDUP = codex_attention.DEDUP_FILENAME
ACTIVE = active_issue.STATE_FILENAME
SESSIONS = session_state.STATE_FILENAME


class _HardKill(BaseException):
    """Escapes every writer's ``except OSError`` the way an OS kill does."""


def _hard_killed_replace(*_args, **_kwargs):  # type: ignore[no-untyped-def]
    raise _HardKill()


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _temps(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


class _StateDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        patcher = unittest.mock.patch.dict(
            os.environ, {hooks_lib.STATE_DIR_ENV_VAR: str(self.dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    # One call per writer, each landing in ``self.dir``.
    def write_codex(self) -> None:
        codex_attention._record_once({"session_id": "sid", "turn_id": str(time.time_ns())})

    def write_active(self) -> None:
        active_issue.write_rows(self.dir / ACTIVE, {"fleet-config#864": {"number": 864}})

    def write_sessions(self) -> None:
        session_state._write_rows(self.dir / SESSIONS, {"sid": {"status": "working"}})

    def writers(self) -> dict[str, tuple[Callable[[], None], object]]:
        """target filename -> (writer, module whose ``os.replace`` it calls)."""
        return {
            DEDUP: (self.write_codex, codex_attention),
            ACTIVE: (self.write_active, active_issue),
            SESSIONS: (self.write_sessions, session_state),
        }


class HardKillNamingTests(_StateDirCase):
    """The temp a killed writer strands must name the file it was replacing."""

    def test_every_writer_strands_a_target_named_temp(self) -> None:
        for target, (write, module) in self.writers().items():
            with self.subTest(target=target):
                before = set(_temps(self.dir))
                with unittest.mock.patch.object(module.os, "replace", _hard_killed_replace):
                    with self.assertRaises(_HardKill):
                        write()
                stranded = sorted(set(_temps(self.dir)) - before)
                self.assertEqual(len(stranded), 1, f"expected one orphan, got {stranded}")
                self.assertTrue(
                    stranded[0].startswith(f".{target}."),
                    f"orphan {stranded[0]!r} does not name its target {target!r}",
                )


class SweepTests(_StateDirCase):
    """The next write expires a dead writer's orphan and nothing else."""

    def test_each_writer_sweeps_its_own_stale_orphan_but_not_its_live_temp(self) -> None:
        for target, (write, _module) in self.writers().items():
            with self.subTest(target=target):
                stale = self.dir / f".{target}.deadbeef.tmp"
                live = self.dir / f".{target}.livewrit.tmp"
                stale.write_text("{}", encoding="utf-8")
                live.write_text("{}", encoding="utf-8")
                _age(stale, STALE_AGE_SECONDS)

                write()

                self.assertFalse(stale.exists(), f"{target}: stale orphan survived")
                self.assertTrue(live.exists(), f"{target}: sweep ate a live in-flight temp")
                live.unlink()

    def test_sweep_never_touches_another_writers_temps_live_or_stale(self) -> None:
        """Prefix scoping, not age, is what protects siblings in the shared dir."""
        for target, (write, _module) in self.writers().items():
            with self.subTest(target=target):
                siblings = [
                    self.dir / f".{other}.{kind}.tmp"
                    for other in self.writers() if other != target
                    for kind in ("livewrit", "deadbeef")
                ]
                siblings += [
                    self.dir / "tmpa1b2c3d4.tmp",                       # legacy anonymous shape
                    self.dir / f".{target}.bak.deadbeef.tmp",           # name that only *starts* like ours
                    self.dir / f".{target}deadbeef.tmp",                # missing separator
                    self.dir / f".{target}.deadbeef.tmp.keep",          # not a .tmp
                    self.dir / "chief-managed.json",                    # a real state file
                ]
                for victim in siblings:
                    victim.write_text("{}", encoding="utf-8")
                for victim in siblings:
                    if "livewrit" not in victim.name:
                        _age(victim, STALE_AGE_SECONDS)

                write()

                for survivor in siblings:
                    self.assertTrue(survivor.exists(),
                                    f"{target} sweep removed {survivor.name}, which it does not own")
                for survivor in siblings:
                    survivor.unlink()

    def test_clean_writes_leave_no_temporaries_and_land_payloads(self) -> None:
        self.write_codex()
        self.write_active()
        self.write_sessions()
        self.assertEqual(_temps(self.dir), [])
        self.assertEqual(len(json.loads((self.dir / DEDUP).read_text(encoding="utf-8"))), 1)
        self.assertIn("fleet-config#864",
                      json.loads((self.dir / ACTIVE).read_text(encoding="utf-8")))


class TreeMirrorTests(unittest.TestCase):
    """``hooks/`` and ``skills/_lib`` carry deliberate copies; they must not drift."""

    def test_both_trees_agree_on_prefix_threshold_and_scope(self) -> None:
        self.assertEqual(hooks_lib.ATOMIC_TMP_SWEEP_AFTER_SECONDS,
                         hooks_state.ATOMIC_TMP_SWEEP_AFTER_SECONDS)
        target = Path("x") / ACTIVE
        self.assertEqual(hooks_lib.atomic_tmp_prefix(target), hooks_state.atomic_tmp_prefix(target))

        names = [f".{ACTIVE}.deadbeef.tmp", f".{ACTIVE}.bak.deadbeef.tmp",
                 "tmpa1b2c3d4.tmp", f".{DEDUP}.deadbeef.tmp", f".{ACTIVE}.x_9.tmp"]
        survivors: list[list[str]] = []
        for sweep in (hooks_lib.sweep_stale_atomic_temps, hooks_state.sweep_stale_atomic_temps):
            with tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                for name in names:
                    (directory / name).write_text("{}", encoding="utf-8")
                    _age(directory / name, STALE_AGE_SECONDS)
                sweep(directory / ACTIVE)
                survivors.append(sorted(p.name for p in directory.iterdir()))
        self.assertEqual(survivors[0], survivors[1])
        self.assertEqual(survivors[0], sorted(names[1:4]))


if __name__ == "__main__":
    unittest.main()
