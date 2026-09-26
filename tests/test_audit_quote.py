"""Unit tests for skills/_lib/audit_quote.py (fleet-config#960 A).

A `/codebase-audit` finding must quote the line it is about, and the quote is
checked against the named file before filing. Synthetic repo only.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_audit_quote.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import audit_quote as aq  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check

tmp = Path(tempfile.mkdtemp(prefix="audit_quote_"))
try:
    (tmp / "pkg").mkdir()
    (tmp / "pkg" / "mod.py").write_bytes(b"def f(x):\r\n    return   x  +  1   \r\n\r\ndef g():\r\n    pass\r\n")
    (tmp / "pkg" / "bin.dat").write_bytes(b"\xff\xfe\x00")
    body = "\n".join([
        "## Findings",
        "",
        "- [ ] **pkg/mod.py:2** — off by one. Quote: `return x + 1`. Fix: drop the +1.",
        "- [ ] **pkg/mod.py:5** — dead. Quote: `def h():`. Fix: delete.",
        "- [ ] **pkg/mod.py:4** — no quote given. Fix: delete.",
        "- [ ] **pkg/gone.py:1** — file missing. Quote: `x`. Fix: n/a.",
        "- [ ] **pkg/bin.dat:1** — binary. Quote: `x`. Fix: n/a.",
        "- [x] **pkg/mod.py:9** — already fixed, never rechecked. Quote: `nope`.",
        "- [ ] **pkg/mod.py:7** — old. _(carried — not re-verified since 2026-09-01)_<!-- last-seen: 2026-09-01 -->",
        "",
        "## Audit run log",
    ])
    got = [(v.path, v.line, v.status) for v in aq.check_body(body, tmp)]
    check(got == [("pkg/mod.py", 2, "VERIFIED"), ("pkg/mod.py", 5, "MISMATCH"), ("pkg/mod.py", 4, "NO_QUOTE"),
                  ("pkg/gone.py", 1, "UNREADABLE"), ("pkg/bin.dat", 1, "UNREADABLE")],
          f"verdicts: whitespace/CRLF-tolerant match, mismatch, no quote and unreadable are distinct; "
          f"ticked and carried items are skipped -- {got}")

    ok_body = tmp / "ok.md"
    ok_body.write_text(body.splitlines()[2] + "\n", encoding="utf-8")
    check(aq.main(["check", "--repo-path", str(tmp), "--body-file", str(ok_body)]) == 0,
          "check exits 0 when every finding is VERIFIED")
    bad_body = tmp / "bad.md"
    bad_body.write_text(body, encoding="utf-8")
    check(aq.main(["check", "--repo-path", str(tmp), "--body-file", str(bad_body)]) == 1,
          "check exits 1 when any finding is not VERIFIED")
    check(aq.main(["check", "--repo-path", str(tmp), "--body-file", str(tmp / "missing.md")]) == 2,
          "an unreadable body is unknown (exit 2), never a pass")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

_h.report_and_exit("test_audit_quote")
