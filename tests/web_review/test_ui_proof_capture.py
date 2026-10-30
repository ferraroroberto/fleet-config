"""Tests for `ui_proof capture` in skills/_lib/ui_proof: arguments, probes and the hand-off to the store.

Playwright is not in this repo's venv, so the browser child (`shoot.py`) is replaced by a stub that
writes the same `shots.json` contract; what is under test is everything around it: the viewport and
theme arguments (phone 390x844 and desktop 1280x900, light and dark by default), the distinct
failures (no interpreter, no Playwright, app not answering, a leg that failed), staging into the
checkout's own `.proof/`, and the scratch folder going away afterwards.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/web_review/test_ui_proof_capture.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
from git_run import run_git  # noqa: E402
from ui_proof import capture_run, cli  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import init_repo  # noqa: E402

_h = CheckHarness()
check = _h.check

STUB = '''\
import json, sys
from pathlib import Path
a = sys.argv[1:]
def vals(flag): return [a[i + 1] for i, x in enumerate(a) if x == flag]
out = Path(vals("--out")[0])
Path(out.parent / "argv.json").write_text(json.dumps(a), encoding="utf-8")
shots = []
for spec in vals("--viewport"):
    label = spec.partition("=")[0]
    for theme in vals("--theme"):
        if label == "broken":
            shots.append({"viewport": label, "theme": theme, "error": "boom"})
            continue
        png = out / f"{label}-{theme}.png"
        png.write_bytes(b"\\x89PNG\\r\\n\\x1a\\nfake")
        shots.append({"viewport": label, "theme": theme, "screenshot": str(png)})
(out / "shots.json").write_text(json.dumps(shots), encoding="utf-8")
'''


def run(*argv: str):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(list(argv))
    return code, buf.getvalue()


class Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server's hook name
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:
        pass


# Viewport arguments: the two names map to their sizes, a bare size labels itself, a bad one is refused.
check(capture_run.parse_viewport("phone") == ("phone", "390x844"), "phone is 390x844")
check(capture_run.parse_viewport("desktop") == ("desktop", "1280x900"), "desktop is 1280x900")
check(capture_run.parse_viewport("390x844") == ("390x844", "390x844"), "a bare size labels itself")
check(capture_run.parse_viewport("tablet=820x1180") == ("tablet", "820x1180"), "label=size is kept")
for bad in ("huge", "a-b=10x10", "phone=big"):
    try:
        capture_run.parse_viewport(bad)
        check(False, f"{bad!r} must be refused")
    except ValueError:
        check(True, f"{bad!r} is refused")

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_address[1]}/"
repo = init_repo(empty_commit=False)
stub_dir = Path(tempfile.mkdtemp(prefix="ui_proof_stub_"))
real = (capture_run._interpreter, capture_run._has_playwright, capture_run.SHOOT)
try:
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".proof/\n", encoding="utf-8")
    run_git(["-C", str(repo), "add", "-A"])
    run_git(["-C", str(repo), "commit", "-q", "-m", "base"])
    run_git(["-C", str(repo), "checkout", "-q", "-b", "feat/9-sheet"])
    root = repo / ".proof"

    # No interpreter, then no Playwright under it: each its own reason, nothing staged.
    code, raw = run("capture", str(repo), "--phase", "after", "--url", url, "--state", "home.sheet")
    check(code == 1 and "PLAYWRIGHT_MISSING no .venv python" in raw, f"no .venv python is its own reason — {raw!r}")
    capture_run._interpreter = lambda _repo: Path(sys.executable)
    capture_run._has_playwright = lambda _python: False
    code, raw = run("capture", str(repo), "--phase", "after", "--url", url, "--state", "home.sheet")
    check(code == 1 and "playwright is not importable" in raw, f"missing Playwright is its own reason — {raw!r}")

    # An app that does not answer is not a browser failure.
    capture_run._has_playwright = lambda _python: True
    code, raw = run("capture", str(repo), "--phase", "after", "--url", "http://127.0.0.1:9/", "--state", "home.sheet")
    check(code == 1 and "APP_UNREACHABLE" in raw, f"an unreachable app is named — {raw!r}")
    code, raw = run("capture", str(repo), "--phase", "after", "--url", url, "--state", "s", "--viewport", "huge")
    check(code == 2 and "CAPTURED=none" in raw, f"a bad viewport is refused before any browser starts — {raw!r}")

    # Defaults: phone and desktop, light and dark, staged under this checkout's .proof/.
    (stub_dir / "shoot.py").write_text(STUB, encoding="utf-8")
    capture_run.SHOOT = stub_dir / "shoot.py"
    code, raw = run("capture", str(repo), "--phase", "after", "--url", url, "--state", "home.sheet", "--click", "#open")
    staging = root / "_branch" / "feat-9-sheet"
    legs = sorted(p.name for p in staging.glob("after-*.png"))
    check(code == 0 and legs == ["after-home.sheet-desktop-dark.png", "after-home.sheet-desktop-light.png",
                                 "after-home.sheet-phone-dark.png", "after-home.sheet-phone-light.png"],
          f"capture stages phone and desktop, light and dark — {raw!r} {legs}")
    check(not [p for p in root.iterdir() if p.name.startswith(".shots-")], "the scratch folder is removed")
    data = json.loads((staging / "proof.json").read_text(encoding="utf-8"))
    check({(r["state"], r["viewport"], r["theme"]) for r in data["after"]}
          == {("home.sheet", v, t) for v in ("phone", "desktop") for t in ("light", "dark")},
          "proof.json records state, viewport and theme per leg")

    # Explicit viewports and themes reach the browser child as given, with the clicks in order.
    code, raw = run("capture", str(repo), "--phase", "before", "--url", url, "--state", "home.sheet",
                    "--viewport", "390x844", "--theme", "dark", "--click", "#open", "--click", ".tab-2")
    check(code == 0 and (staging / "before-home.sheet-390x844-dark.png").is_file()
          and not (staging / "before-home.sheet-390x844-light.png").exists(),
          f"--viewport and --theme narrow the legs — {raw!r}")
    argv = json.loads((root / "argv.json").read_text(encoding="utf-8"))
    check(argv.count("--viewport") == 1 and "390x844=390x844" in argv and argv.count("--theme") == 1
          and [argv[i + 1] for i, x in enumerate(argv) if x == "--click"] == ["#open", ".tab-2"],
          f"the child gets the size, the theme and the clicks in order — {argv}")

    # A failed leg is reported by name and the others still land.
    code, raw = run("capture", str(repo), "--phase", "before", "--url", url, "--state", "x",
                    "--viewport", "broken=100x100", "--viewport", "phone", "--theme", "light")
    check(code == 1 and "failed_legs=broken-light" in raw and (staging / "before-x-phone-light.png").is_file(),
          f"a failed leg is named while the rest are kept — {raw!r}")

    # Nothing outside this checkout, nothing tracked.
    status = run_git(["-C", str(repo), "status", "--porcelain"]).stdout.strip()
    check(status == "", f"the tree stays clean — got {status!r}")
finally:
    capture_run._interpreter, capture_run._has_playwright, capture_run.SHOOT = real
    server.shutdown()
    for path in (repo, stub_dir):
        shutil.rmtree(path, ignore_errors=True)


_h.report_and_exit("ui_proof_capture")
