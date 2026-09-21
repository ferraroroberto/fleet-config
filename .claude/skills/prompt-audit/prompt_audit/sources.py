"""Vendor-guide freshness gate: hash a fetched guide against its baseline.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import re
from typing import Optional

from .common import clean, sha12


# ---- freshness gate -------------------------------------------------------------

_DEFAULT_MODEL_LIST = r"including ((?:[^.]|\.\d)+)\."


def extract_marker(kind: str, text: str, cfg: dict) -> Optional[str]:
    """The source's version marker, "" for kind `none`, None when not extractable."""
    if kind == "none":
        return ""
    if kind == "model-list":
        m = re.search(cfg.get("marker_pattern") or _DEFAULT_MODEL_LIST, text)
        return clean(m.group(1)) if m else None
    if kind == "llms-txt-lines":
        pattern = cfg.get("marker_pattern")
        if not pattern:
            return None
        found = sorted(set(re.findall(pattern, text)))
        return ",".join(found) if found else None
    if kind == "latest-model-frontmatter":
        if not text.startswith("---"):
            return None
        end = text.find("\n---", 3)
        block = text[3:end] if end != -1 else ""
        key = re.escape(cfg.get("marker_key", "model"))
        m = re.search(rf"^\s*{key}:\s*(\S+)\s*$", block, re.MULTILINE)
        return m.group(1) if m else None
    raise ValueError(f"unknown marker_kind {kind!r}")


def diff_source(cfg: dict, data: Optional[bytes], final_url: Optional[str] = None) -> dict:
    """Verdict for one fetched source against its baseline.

    `data` None / empty -> `not-checked`: an unfetched page is never `unchanged`.
    An `index` source is judged on its marker alone (its sha moves with every new
    docs page); a gained marker line is `new-guide`, a lost one `changed`. A
    `latest-model-frontmatter` marker change is `new-guide`. Otherwise the sha
    decides. A moved redirect target is `changed` in its own right.
    """
    kind = cfg.get("marker_kind", "none")
    if not data:
        return {"verdict": "not-checked", "sha": "unmeasured", "marker": "unmeasured",
                "reason": "fetched file missing or empty"}
    text = data.decode("utf-8", errors="replace")
    sha = sha12(data)
    marker = extract_marker(kind, text, cfg)
    shown = "unmeasured" if marker is None else (marker or "none")
    out = {"sha": sha, "marker": shown}
    baseline_sha = cfg.get("baseline_sha", "")
    baseline_marker = cfg.get("baseline_marker", "")
    expected_url = cfg.get("baseline_final_url")
    if final_url and expected_url and final_url != expected_url:
        return {**out, "verdict": "changed", "reason": f"redirect target moved to {final_url}"}
    if kind == "llms-txt-lines":
        if marker is None:
            return {**out, "verdict": "changed", "reason": "no guide lines found in the index"}
        now, before = set(marker.split(",")), set(filter(None, baseline_marker.split(",")))
        if now - before:
            return {**out, "verdict": "new-guide", "reason": "new guide lines: " + ",".join(sorted(now - before))}
        if before - now:
            return {**out, "verdict": "changed", "reason": "guide lines removed: " + ",".join(sorted(before - now))}
        moved = "index sha moved, guide lines identical" if sha != baseline_sha else "identical"
        return {**out, "verdict": "unchanged", "reason": moved}
    if kind == "latest-model-frontmatter" and marker != baseline_marker:
        return {**out, "verdict": "new-guide", "reason": f"flagship marker {baseline_marker} -> {shown}"}
    if sha != baseline_sha:
        reason = f"sha {baseline_sha} -> {sha}"
        if kind == "model-list" and marker != baseline_marker:
            reason += "; model list changed"
        return {**out, "verdict": "changed", "reason": reason}
    return {**out, "verdict": "unchanged", "reason": "identical"}
