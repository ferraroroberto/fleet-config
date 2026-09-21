"""Collapse findings shared with the scaffolding master and the lite global.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .common import HOME_REPO, LITE_REPO, MASTER_REPO


# ---- scaffold dedup -------------------------------------------------------------

_MARKUP = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+|[*_`>#]")


def norm_line(s: str) -> str:
    return re.sub(r"\s+", " ", _MARKUP.sub("", s)).strip().lower()


def dedup(findings: List[dict], master_text: str, lite_text: str,
          master_key: str = f"{MASTER_REPO}/CLAUDE.md",
          global_key: str = f"{HOME_REPO}/global-CLAUDE.md") -> List[dict]:
    """Annotate each finding with where it should be filed.

    `shared-with-scaffold`: the offending line also appears (normalised) in the
    scaffolding master, so it is filed once there with a `propagate_to` list of
    every repo carrying it. `shared-with-lite`: a global-file line that also sits
    in the lite port's global instructions. Otherwise `repo-local`. Lines under
    20 normalised characters never match — too generic to call shared.
    """
    master = {norm_line(l) for l in master_text.splitlines() if len(norm_line(l)) >= 20}
    lite = {norm_line(l) for l in lite_text.splitlines() if len(norm_line(l)) >= 20}
    groups: Dict[Tuple[str, str], set] = {}
    out = []
    for f in findings:
        n = norm_line(f.get("text", ""))
        repo = f["path"].split("/", 1)[0]
        g = dict(f)
        if f["path"] != master_key and len(n) >= 20 and n in master:
            g.update(scope="shared-with-scaffold", file_against=master_key)
            groups.setdefault((f["rule"], n), set()).add(repo)
        elif f["path"] == global_key and len(n) >= 20 and n in lite:
            g.update(scope="shared-with-lite", file_against=global_key, propagate_to=[LITE_REPO])
        else:
            g.update(scope="scaffold-master" if f["path"] == master_key else "repo-local",
                     file_against=f["path"])
        out.append(g)
    for g in out:
        n = norm_line(g.get("text", ""))
        if g["scope"] in ("shared-with-scaffold", "scaffold-master"):
            g["propagate_to"] = sorted(groups.get((g["rule"], n), set()))
    return out
