"""The generated repo list in `docs/fleet-guide.md`.

Hard: the inputs are this repo's committed `architecture/fleet.data.js` and the
guide itself, so a sibling repo's commit cannot turn it red. `/system-map`
regenerates both in the same run, so a fresh map never leaves the guide stale.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Tuple

from acceptance.shared import REPO, _Checker

_SYSTEM_MAP = REPO / ".claude" / "skills" / "system-map"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _fleet_guide_check() -> Tuple[int, int]:
    """Every mapped repo is listed once with its own description, and the block is fresh."""
    check = _Checker()
    sys.path.insert(0, str(_SYSTEM_MAP))
    rg = _load("system_map_render_guide", _SYSTEM_MAP / "render_guide.py")

    check("fleet_guide: a card description renders as plain Markdown",
          rg.plain("<b>Hub</b> — a &amp; b | c") == "**Hub** — a & b \\| c", rg.plain("<b>Hub</b> — a &amp; b | c"))

    data = {"governance": [{"ic": "📐", "nm": "scaffold", "ds": "Template."}],
            "enabling": [{"ic": "🧠", "nm": "Hub Lite", "repo": "hub-lite", "port": 8000, "ds": "A hub."}],
            "web": [], "pipe": []}
    block = rg.render_block(data)
    check("fleet_guide: each card is one row under its layer, by repo name, with its port",
          "| 📐 | `scaffold` | Template. | — |" in block and "| 🧠 | `hub-lite` | A hub. | :8000 |" in block
          and "(`layer = \"working-pipe\"`, 0 repos)" in block, block)

    guide = rg.GUIDE.read_text(encoding="utf-8")
    try:
        rg.splice("no markers here", block)
        check("fleet_guide: a guide without the markers is refused", False, "accepted")
    except ValueError:
        check("fleet_guide: a guide without the markers is refused", True)

    real = rg.load_data(rg.DATA_JS.read_text(encoding="utf-8"))
    missing = [c.get("repo", c["nm"]) for s in rg.SECTIONS for c in real.get(s, [])
               if f"| `{c.get('repo', c['nm'])}` | {rg.plain(c['ds'])} |" not in guide]
    check("fleet_guide: every repo in fleet.data.js is listed with its current description",
          not missing, "missing or drifted: " + ", ".join(missing))
    check("fleet_guide: docs/fleet-guide.md matches render_guide.py output", rg.regenerate() == guide,
          "regenerate: E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render_guide.py")
    return check.failures, check.total
