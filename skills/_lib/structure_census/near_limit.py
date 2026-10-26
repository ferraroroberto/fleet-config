"""Files and functions just under a repo's structure limits, for the weekly `/audit-fleet` digest.

A split that stops as soon as the gate passes leaves code one edit from failing it, and an empty
structure baseline no longer shows that. This counts, per repo that enables the structure ratchet,
the own-code files and functions inside the last tenth below that repo's `max_file_lines` and
`max_function_lines`, using the census's file and function definitions (`measure`) and band
(`measure.near_band`). "Own" code is what the ratchet itself measures: vendored `dest` paths, the
`exclude` globs and generated files are left out.

The tree is read from `origin/<default>` through `git archive`, never the checkout. Three states
stay distinct and none folds into a zero count: `adopted` (counts), `not_adopted` (the structure
ratchet is not enabled there) and `unknown` (a fact that could not be established).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import git_run  # noqa: E402
import structure_ratchet  # noqa: E402
from fleet_core import fleet_toml  # noqa: E402
from structure_census import measure, repo_read  # noqa: E402


def count_near(blobs: dict[str, bytes], cfg: structure_ratchet.Config, vendored: tuple[str, ...]) -> dict:
    """`{files, fns}` near the limits among `blobs`' own code, or `{"unparsed": [paths]}` when a Python file did not parse.

    A Python file that does not parse has function lengths nobody measured, so the reading is
    withheld rather than counted without it.
    """
    file_band = measure.near_band(cfg.max_file_lines)
    fn_band = measure.near_band(cfg.max_function_lines)
    files = fns = 0
    unparsed: list[str] = []
    for path, raw in blobs.items():
        if not measure.is_code(path) or not structure_ratchet.in_scope(path, cfg, vendored):
            continue
        text = repo_read.text_of(raw)
        if text is None or structure_ratchet.is_generated(raw):
            continue
        files += measure.in_band(measure.count_lines(text), file_band)
        spans = measure.functions(path, text)
        if spans is None:
            unparsed.append(path)
        else:
            fns += sum(measure.in_band(lines, fn_band) for lines, _where in spans)
    return {"unparsed": sorted(unparsed)} if unparsed else {"files": files, "fns": fns}


def read_repo(repo_path: str) -> dict:
    """`{"state": "adopted"|"not_adopted"|"unknown", ...}` for one repo, read from its default branch."""
    repo = Path(repo_path)
    ref = git_run.resolve_default_branch_ref(repo, candidates=("origin/main", "origin/master"), final_fallback="")
    if not ref:
        return {"state": "unknown", "reason": "no origin default branch ref"}
    try:
        blobs = repo_read.read_tree(repo, ref)
    except repo_read.Unknown as exc:
        return {"state": "unknown", "reason": str(exc)}
    raw = blobs.get(fleet_toml.FILENAME)
    if raw is None:
        return {"state": "not_adopted"}
    text = raw.decode("utf-8", "replace")
    try:
        cfg = structure_ratchet.parse_config(text)
        dests = structure_ratchet.vendored_dests(text)
    except (structure_ratchet.ConfigError, tomllib.TOMLDecodeError) as exc:
        return {"state": "unknown", "reason": f"{fleet_toml.FILENAME} cannot be read: {exc}"}
    if cfg is None:
        return {"state": "not_adopted"}
    counted = count_near(blobs, cfg, dests)
    if "unparsed" in counted:
        return {"state": "unknown", "reason": f"{len(counted['unparsed'])} Python file(s) did not parse, e.g. {counted['unparsed'][0]}"}
    return {"state": "adopted", **counted}
