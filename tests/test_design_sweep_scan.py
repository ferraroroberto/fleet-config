"""Unit tests for the pure logic in skills/_lib/design_sweep_scan.py (fleet-config#180).

No live git/gh — these exercise `classify_web_app` over synthetic temp trees
(design_lint's `repo_files` walk falls back to rglob without a git repo, so no
`git init` is needed). The trees here ARE the acceptance cases: a token-styled
FastAPI PWA is swept, a Streamlit-only POC and a non-web repo are skipped, and a
FastAPI app that *also* ships a Streamlit spike is still swept (not misfiled as
Streamlit-only).

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_design_sweep_scan.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import design_sweep_scan as dss  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


def make_tree(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="design_sweep_"))
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


_ROOT_CSS = ":root {\n  --bg: #0d1117;\n  --ink: #e6edf3;\n  --radius: 12px;\n}\n"

# A token-styled FastAPI PWA -> web (sweep it).
web_tree = make_tree({
    "app/webapp/static/styles.css": _ROOT_CSS,
    "app/webapp/server.py": "from fastapi import FastAPI\napp = FastAPI()\n",
})
# A Streamlit-only POC: token CSS but its app is streamlit_app.py, no FastAPI -> streamlit.
streamlit_tree = make_tree({
    "styles.css": _ROOT_CSS,
    "streamlit_app.py": "import streamlit as st\nst.title('poc')\n",
})
# No token-bearing stylesheet at all -> non_web.
non_web_tree = make_tree({
    "README.md": "# A pipeline repo\n",
    "main.py": "print('etl')\n",
})
# A plain .css with no :root custom properties -> non_web (design-sync's own gate).
plain_css_tree = make_tree({
    "static/site.css": "body { margin: 0; }\n.header { color: #333; }\n",
    "server.py": "from fastapi import FastAPI\n",
})
# A real FastAPI PWA that ALSO ships a streamlit spike -> web, NOT streamlit-only.
web_plus_spike_tree = make_tree({
    "app/webapp/static/styles.css": _ROOT_CSS,
    "app/webapp/server.py": "import uvicorn\nfrom fastapi import FastAPI\n",
    "spike/streamlit_app.py": "import streamlit as st\n",
})
# Token CSS living under a spike/ dir is ignored (design_lint SKIP_DIR_PARTS) -> non_web.
spike_only_css_tree = make_tree({
    "spike/styles.css": _ROOT_CSS,
    "spike/streamlit_app.py": "import streamlit as st\n",
})

try:
    check(dss.classify_web_app(web_tree)[0] == "web",
          "token-styled FastAPI PWA -> web (sweep it)")
    check(dss.classify_web_app(streamlit_tree)[0] == "streamlit",
          "streamlit_app.py + no FastAPI -> streamlit (skip)")
    check(dss.classify_web_app(non_web_tree)[0] == "non_web",
          "no stylesheet -> non_web (skip)")
    check(dss.classify_web_app(plain_css_tree)[0] == "non_web",
          "CSS with no :root custom props -> non_web (design-sync's own gate)")
    check(dss.classify_web_app(web_plus_spike_tree)[0] == "web",
          "FastAPI PWA with a streamlit spike -> web, not misfiled as streamlit-only")
    check(dss.classify_web_app(spike_only_css_tree)[0] == "non_web",
          "token CSS only under spike/ -> non_web (SKIP_DIR_PARTS excludes it)")

    # The reason strings distinguish the three categories (distinct messages).
    reasons = {
        dss.classify_web_app(web_tree)[1].split()[0],
        dss.classify_web_app(streamlit_tree)[1].split()[0],
        dss.classify_web_app(non_web_tree)[1].split()[0],
    }
    check(len(reasons) == 3, "each category returns a distinct reason lead-word")

    # Helper predicates.
    check(dss._has_fastapi_signal(web_tree), "_has_fastapi_signal: FastAPI import -> True")
    check(dss._has_fastapi_signal(web_plus_spike_tree), "_has_fastapi_signal: uvicorn -> True")
    check(not dss._has_fastapi_signal(streamlit_tree), "_has_fastapi_signal: streamlit-only -> False")
    check(not dss._is_streamlit_only(web_plus_spike_tree),
          "_is_streamlit_only: FastAPI co-present -> False (not streamlit-only)")
    check(dss._is_streamlit_only(streamlit_tree),
          "_is_streamlit_only: streamlit_app.py, no FastAPI -> True")
finally:
    for t in (web_tree, streamlit_tree, non_web_tree, plain_css_tree,
              web_plus_spike_tree, spike_only_css_tree):
        shutil.rmtree(t, ignore_errors=True)


_h.report_and_exit("design_sweep_scan")
