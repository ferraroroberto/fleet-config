"""Same-name top-level JS definitions across >=2 files (the `siblings` lens).

The 7x-duplicated `schedule(ms)` case: a helper copy-pasted between view
scripts instead of shared, which the design system treats as drift because the
copies inevitably diverge.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from .css import strip_comments
from .files import read_text, rel


_DEF_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|"
    r"^(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\()",
    re.M,
)
_SIBLING_STOPLIST = {"init", "main", "render", "update"}


def siblings(root: Path, js_files: List[Path]) -> List[dict]:
    """Top-level definitions with the same name in >=2 JS files."""
    defs: Dict[str, List[str]] = {}
    for path in js_files:
        text = strip_comments(read_text(path), "js")
        for m in _DEF_RE.finditer(text):
            name = m.group(1) or m.group(2)
            if not name or name in _SIBLING_STOPLIST or len(name) < 3:
                continue
            line = text[: m.start()].count("\n") + 1
            defs.setdefault(name, []).append(f"{rel(root, path)}:{line}")
    dupes = [{"name": name, "sites": sites}
             for name, sites in sorted(defs.items())
             if len({s.split(":")[0] for s in sites}) >= 2]
    return dupes
