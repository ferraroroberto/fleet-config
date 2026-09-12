"""Atomic-write contract for hooks/session_state.py (fleet-config#816).

Two independent defects in ``_write_rows``, covered here because neither is
reachable from the hook-payload acceptance checks in
``tests/acceptance/checks_session_state.py`` — both live below the row-level
behaviour those assert:

* **Stranded temporaries.** A hard kill between ``mkstemp`` and ``os.replace``
  runs no Python handler, so the function's own ``except OSError`` unlink never
  fires. Reproduced 4/8 times by hard-killing a writer mid-loop, in both
  observed forms (zero-byte, and a fully-written payload that never committed).
  Nothing inside the dead process can clean up, so the contract is instead:
  temps name their target, and a later writer expires them by age.
* **A silently discarded write.** An exhausted retry loop used to return
  ``None`` — indistinguishable from a committed write, so a caller could not
  tell fresh Board state from stale.
"""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import time
import unittest
import unittest.mock
from datetime import timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "session_state_under_test", ROOT / "hooks" / "session_state.py")
assert SPEC and SPEC.loader
session_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(session_state)


def _age(path: Path, delta: timedelta) -> None:
    """Backdate a file's mtime by ``delta``."""
    stamp = time.time() - delta.total_seconds()
    os.utime(path, (stamp, stamp))


class TempFileHygieneTests(unittest.TestCase):
    """The temp a hard-killed writer leaves behind must be attributable and finite."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.target = self.dir / session_state.STATE_FILENAME

    def test_temp_is_named_after_the_file_it_is_replacing(self) -> None:
        """An orphan must identify its writer, not read as an anonymous tmp*.tmp."""
        seen: list[str] = []
        real_replace = os.replace

        def spy(src, dst, *args, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(os.path.basename(src))
            return real_replace(src, dst, *args, **kwargs)

        with unittest.mock.patch.object(session_state.os, "replace", spy):
            session_state._write_rows(self.target, {"sid-1": {"status": "working"}})

        self.assertEqual(len(seen), 1)
        self.assertTrue(
            seen[0].startswith(f".{session_state.STATE_FILENAME}."),
            f"temp {seen[0]!r} does not name its target",
        )
        self.assertTrue(seen[0].endswith(".tmp"))

    def test_stale_orphan_is_swept_but_a_live_temp_survives(self) -> None:
        """Age is the only discriminator, and it must not race a concurrent writer."""
        prefix = f".{session_state.STATE_FILENAME}."
        stale = self.dir / f"{prefix}deadbeef.tmp"
        stale.write_text("{}", encoding="utf-8")
        _age(stale, session_state._TMP_SWEEP_AFTER + timedelta(minutes=5))

        live = self.dir / f"{prefix}livewrit.tmp"
        live.write_text("{}", encoding="utf-8")

        session_state._write_rows(self.target, {"sid-1": {"status": "working"}})

        self.assertFalse(stale.exists(), "stale orphan survived the sweep")
        self.assertTrue(live.exists(), "sweep ate a concurrent writer's live temp")

    def test_sweep_is_scoped_to_its_own_target(self) -> None:
        """`hooks/state/` is shared fleet state — a sweep may only touch its own temps."""
        sibling = self.dir / f".{session_state.ENDED_FILENAME}.deadbeef.tmp"
        legacy = self.dir / "tmpa1b2c3d4.tmp"
        unrelated = self.dir / "chief-managed.json"
        for victim in (sibling, legacy, unrelated):
            victim.write_text("{}", encoding="utf-8")
            _age(victim, session_state._TMP_SWEEP_AFTER + timedelta(hours=2))

        session_state._write_rows(self.target, {"sid-1": {"status": "working"}})

        for survivor in (sibling, legacy, unrelated):
            self.assertTrue(survivor.exists(),
                            f"sweep removed {survivor.name}, which it does not own")

    def test_a_successful_write_still_lands_its_payload(self) -> None:
        """The hygiene above must not cost the function its actual job."""
        session_state._write_rows(self.target, {"sid-1": {"status": "working"}})
        self.assertEqual(
            json.loads(self.target.read_text(encoding="utf-8")),
            {"sid-1": {"status": "working"}},
        )
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [], f"clean write left temporaries: {leftovers}")


if __name__ == "__main__":
    unittest.main()
