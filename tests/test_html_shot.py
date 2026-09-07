"""Unit tests for the pure logic in skills/_lib/html_shot.py (fleet-config#96).

No live Chrome — these exercise URL-scheme detection, file:// URL building,
query-string appending, target-URL resolution, and the DIMS-log parser.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_html_shot.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
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


# ---- loopback HTTPS certificate boundary ----

check(hs.is_loopback_https_url("https://127.0.0.1:8445"), "IPv4 loopback HTTPS detected")
check(hs.is_loopback_https_url("https://localhost:8445"), "localhost HTTPS detected")
check(hs.is_loopback_https_url("https://[::1]:8445"), "IPv6 loopback HTTPS detected")
check(not hs.is_loopback_https_url("http://127.0.0.1:8445"), "plain HTTP needs no TLS bypass")
check(not hs.is_loopback_https_url("https://example.com"), "external HTTPS never gets a TLS bypass")


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


# ---- browser discovery + command construction ----

_cache = Path(tempfile.mkdtemp(prefix="html-shot-cache-"))
try:
    old = _cache / "chromium_headless_shell-1217" / "chrome-headless-shell-win64"
    new = _cache / "chromium_headless_shell-1234" / "chrome-headless-shell-win64"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "chrome-headless-shell.exe").write_text("", encoding="utf-8")
    (new / "chrome-headless-shell.exe").write_text("", encoding="utf-8")
    check(
        hs.find_cached_headless_shell(_cache) == new / "chrome-headless-shell.exe",
        "newest Playwright headless-shell revision wins",
    )
finally:
    shutil.rmtree(_cache, ignore_errors=True)

shell_cmd = hs.build_browser_command(
    "chrome-headless-shell.exe", True, "https://127.0.0.1:8445", ["--screenshot=x.png"]
)
check("--headless=new" not in shell_cmd, "headless shell does not receive full-Chrome headless mode")
check("--ignore-certificate-errors" in shell_cmd and "--test-type" in shell_cmd,
      "loopback HTTPS receives the scoped certificate bypass")

chrome_cmd = hs.build_browser_command(
    "chrome.exe", False, "https://example.com", ["--screenshot=x.png"]
)
check("--headless=new" in chrome_cmd, "full-Chrome fallback enables headless mode")
check("--ignore-certificate-errors" not in chrome_cmd and "--test-type" not in chrome_cmd,
      "external HTTPS never receives the certificate bypass")

_saved_shell = hs.find_headless_shell
_saved_chrome = hs.find_chrome
try:
    hs.find_headless_shell = lambda: None
    hs.find_chrome = lambda: "fallback-chrome.exe"
    check(hs.find_browser() == ("fallback-chrome.exe", False),
          "missing headless shell degrades gracefully to full Chrome")
finally:
    hs.find_headless_shell = _saved_shell
    hs.find_chrome = _saved_chrome


_h.report_and_exit("html_shot")
