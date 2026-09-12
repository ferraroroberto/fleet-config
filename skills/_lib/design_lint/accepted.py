"""Accepted design-drift exceptions — a repo's `[[design.accepted]]` table.

A contract finding can be accurate and its proposed fix still wrong for one
repo: local-llm-hub-lite's `app-icon-family` FAIL (no `brand_gen.render_set`
caller) is true, but its icons are byte-identical copies of upstream
local-llm-hub's generated set and the fork refuses the generator dependency.
With nowhere to record that verdict, `/design-sync` re-filed it four times
(lite#1/#18/#21/#23, fleet-config#836).

A repo declares the verdict once, in its own `.fleet.toml`:

    [[design.accepted]]
    check        = "app-icon-family"                # contracts id
    target       = "app_web/static/index.html"      # the finding's evidence file
    detail       = "shared brand_gen.render_set generator not adopted"
    reason       = "byte-identical copies of upstream's generated set"
    record       = "https://github.com/<owner>/<repo>/issues/23"   # optional
    identical_to = "../local-llm-hub"               # optional assertion ...
    paths        = ["app_web/static/icon-180.png"]  # ... re-checked every run

Three rules keep an exception from becoming permanent blindness:

- It matches only the exact `detail` it accepted. A new problem in the same
  check changes the detail, so the finding is raised again.
- A declared assertion is re-verified every run: each path's committed blob
  must hash identical in both repos. A mismatch, or anything that stops the
  comparison being made at all (missing sibling repo, unreadable blob), keeps
  the finding raised — unknown is never accepted.
- Nothing is dropped. An accepted finding stays in the output as `ACCEPTED`;
  a declaration that matches nothing, or is malformed, becomes a WARN row.

Committed blobs, not the working tree, for the same reason as
`vendored_drift`: these checkouts store LF and check out CRLF, so a
filesystem read of a text file would differ from its own blob.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import List, Optional, Tuple

# Sibling top-level module in skills/_lib, reached as `files.py` reaches it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402


ROW_ID = "accepted-exception"
_REQUIRED = ("check", "target", "detail", "reason")
_LINE_SUFFIX_RE = re.compile(r":\d+$")


def _row(detail: str) -> dict:
    return {"id": ROW_ID, "status": "WARN", "detail": detail, "evidence": ".fleet.toml"}


def load_accepted(root: Path) -> Tuple[List[dict], List[dict]]:
    """Valid `[[design.accepted]]` entries, plus a WARN row per unusable one.

    No `.fleet.toml`, or one without a `design` table, is `([], [])` — a repo
    that declares nothing behaves exactly as before."""
    path = root / ".fleet.toml"
    if not path.is_file():
        return [], []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [], [_row(f"could not read .fleet.toml, so no accepted exception "
                         f"applies: {exc}")]
    design = data.get("design")
    if design is None:
        return [], []
    raw = design.get("accepted") if isinstance(design, dict) else None
    if not isinstance(raw, list):
        return [], [_row("[design] must hold an array of [[design.accepted]] tables")]

    entries: List[dict] = []
    problems: List[dict] = []
    for index, entry in enumerate(raw, start=1):
        label = f"[[design.accepted]] #{index}"
        if not isinstance(entry, dict):
            problems.append(_row(f"{label} is not a table"))
            continue
        missing = [k for k in _REQUIRED
                   if not isinstance(entry.get(k), str) or not entry[k].strip()]
        if missing:
            problems.append(_row(f"{label} ignored: missing {', '.join(missing)}"))
            continue
        if "identical_to" in entry or "paths" in entry:
            upstream, paths = entry.get("identical_to"), entry.get("paths")
            if not (isinstance(upstream, str) and upstream.strip()
                    and isinstance(paths, list) and paths
                    and all(isinstance(p, str) and p.strip() for p in paths)):
                problems.append(_row(f"{label} ignored: identical_to and paths go together "
                                     "(a repo path and a non-empty list of file paths)"))
                continue
        entries.append({**entry, "_label": label})
    return entries, problems


def _blob_sha256(repo: Path, path: str) -> Optional[str]:
    try:
        out = git_run.run_git_bytes(["-C", str(repo), "show", f"HEAD:{path}"], timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return hashlib.sha256(out.stdout).hexdigest()


def verify(root: Path, entry: dict) -> Tuple[str, str]:
    """(`pass` | `fail` | `unknown` | `none`, why) for an entry's assertion."""
    if "identical_to" not in entry:
        return "none", "no verifiable assertion declared"
    upstream = (root / entry["identical_to"]).resolve()
    if not upstream.is_dir():
        return "unknown", f"{entry['identical_to']} not found"
    mismatched: List[str] = []
    for path in entry["paths"]:
        ours, theirs = _blob_sha256(root, path), _blob_sha256(upstream, path)
        if ours is None or theirs is None:
            side = "this repo" if ours is None else entry["identical_to"]
            return "unknown", f"could not read committed {path} in {side}"
        if ours != theirs:
            mismatched.append(path)
    if mismatched:
        return "fail", (f"sha256 differs from {entry['identical_to']}: "
                        + ", ".join(mismatched))
    return "pass", (f"sha256 identical to {entry['identical_to']} for "
                    f"{len(entry['paths'])} path(s)")


def _matches(entry: dict, finding: dict) -> bool:
    evidence = _LINE_SUFFIX_RE.sub("", finding.get("evidence") or "")
    return (finding.get("status") in ("WARN", "FAIL")
            and finding.get("id") == entry["check"]
            and evidence == entry["target"]
            and finding.get("detail") == entry["detail"])


def apply_accepted(root: Path, checks: List[dict]) -> List[dict]:
    """Mark findings covered by a live exception `ACCEPTED`, never drop them.

    A matched finding whose assertion fails or cannot be established keeps
    its WARN/FAIL status, with the reason appended to its detail. Declarations
    that match no finding, or cannot be used, are appended as WARN rows."""
    entries, out_rows = load_accepted(root)
    if not entries and not out_rows:
        return checks
    used = set()
    result: List[dict] = []
    for finding in checks:
        entry = next((e for e in entries
                      if e["_label"] not in used and _matches(e, finding)), None)
        if entry is None:
            result.append(finding)
            continue
        used.add(entry["_label"])
        state, why = verify(root, entry)
        if state in ("fail", "unknown"):
            result.append({
                **finding,
                "detail": (f"{finding['detail']} [accepted exception not applied, "
                           f"assertion {state}: {why}]"),
                "exception": {"state": f"verify-{state}", "why": why},
            })
            continue
        result.append({
            **finding,
            "status": "ACCEPTED",
            "accepted": {
                "raised_status": finding["status"],
                "reason": entry["reason"],
                "record": entry.get("record", ""),
                "verified": why,
            },
        })
    for entry in entries:
        if entry["_label"] not in used:
            out_rows.append(_row(
                f"{entry['_label']} ({entry['check']} on {entry['target']}) matched no "
                "current WARN/FAIL finding with that exact detail; update or remove it"))
    return result + out_rows
