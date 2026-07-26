"""Unit tests for the pure logic in skills/_lib/html_shot.py (fleet-config#96).

No live Chrome — these exercise URL-scheme detection, file:// URL building,
query-string appending, target-URL resolution, and the DIMS-log parser.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_html_shot.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import html_shot as hs  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


# ---- is_url ----

check(hs.is_url("http://localhost:8000/board"), "http url detected")
check(hs.is_url("https://example.com"), "https url detected")
check(hs.is_url("file:///E:/automation/fleet-config/x.html"), "file url detected")
check(not hs.is_url("E:/automation/fleet-config/x.html"), "a plain windows path is not a url")
check(not hs.is_url("architecture/system-map.html"), "a plain relative path is not a url")
check(not hs.is_url("/settings"), "a bare route (no scheme) is not a url")


# ---- to_file_url ----

url = hs.to_file_url(Path("E:/automation/fleet-config/architecture/system-map.html"))
check(url.startswith("file:///"), "file url has the file:/// prefix")
check("\\" not in url, "file url has no backslashes")
check(url.endswith("system-map.html"), "file url preserves the filename")


# ---- append_query ----

check(hs.append_query("file:///x.html", "placeholders=1") == "file:///x.html?placeholders=1",
      "query appended with ? when none present")
check(hs.append_query("http://host/board?tab=1", "placeholders=1")
      == "http://host/board?tab=1&placeholders=1",
      "query appended with & when one already present")
check(hs.append_query("file:///x.html", None) == "file:///x.html", "no query -> unchanged")
check(hs.append_query("file:///x.html", "") == "file:///x.html", "empty query -> unchanged")


# ---- build_target_url ----

built = hs.build_target_url(Path("E:/automation/fleet-config/architecture/system-map.html"), "placeholders=1")
check(built.startswith("file:///") and built.endswith("?placeholders=1"),
      "a Path target builds a file:// url with the query appended")

live = hs.build_target_url("http://127.0.0.1:8000/board", "placeholders=1")
check(live == "http://127.0.0.1:8000/board?placeholders=1",
      "an already-live url string is used as-is, query appended")

live_no_query = hs.build_target_url("http://127.0.0.1:8000/board", None)
check(live_no_query == "http://127.0.0.1:8000/board", "a live url with no query is untouched")

plain_str = hs.build_target_url("architecture/system-map.html", None)
check(plain_str.startswith("file:///"), "a plain path string (no scheme) still builds a file:// url")


# ---- parse_dims ----

check(hs.parse_dims(b"some log noise\nDIMS 1760 1170\nmore noise") == (1760, 1170),
      "DIMS extracted from surrounding log noise")
check(hs.parse_dims(b"no dims line here at all") is None, "no DIMS line -> None")
check(hs.parse_dims(b"") is None, "empty stderr -> None")


_h.report_and_exit("html_shot")
