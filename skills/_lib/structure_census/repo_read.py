"""Measure one repo at a named ref, reading its tree through `git archive` and nothing else.

The ref is `origin/main` (else `origin/master`, else `HEAD`) as last fetched; nothing is
fetched. With an `as_of` timestamp the ref is the last first-parent commit on it at or
before that moment, which is how an earlier census is re-run. A repo whose facts cannot
be read is `unknown` with its reason, never measured as zero.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402
from fleet_core import fleet_toml  # noqa: E402
from gate_coverage import reader as ratchet_reader  # noqa: E402
from structure_census import measure  # noqa: E402
from structure_debt import baseline as structure_baseline  # noqa: E402

REFS = ("origin/main", "origin/master", "HEAD")
GIT_TIMEOUT_S = 300
FILE_LIMIT, BIG_FILE_LIMIT = 1000, 2000
FN_LIMITS = (100, 200, 300)
# "Just under the limit": the band a split aims for when it stops as soon as the gate passes.
NEAR_FILE, NEAR_FN = (900, FILE_LIMIT), (90, FN_LIMITS[0])
TOP_FUNCTIONS = 10
# A gate file, and the checks the census recognises inside one.
GATE_FILE_NAMES = ("verify-before-ship.ps1", "run_acceptance.py", "Makefile")
GATE_KEYWORDS = (
    "ruff check", "ruff format", "mypy", "pyright", "eslint", "compileall", "pytest", "playwright",
    "classify_e2e", "import-linter", "lint-imports", *ratchet_reader.RATCHETS,
)


class Unknown(Exception):
    """A fact about the repo that could not be established; the message is the reason."""


def _git(repo: Path, *args: str) -> str:
    res = git_run.run_git(["-C", str(repo), *args], timeout=GIT_TIMEOUT_S)
    if res.returncode != 0:
        raise Unknown(f"git {args[0]} failed: {(res.stderr or '').strip()[:200]}")
    return res.stdout.strip()


def resolve_ref(repo: Path, as_of: Optional[str]) -> tuple[str, str, str]:
    """`(ref name, full sha, commit time)` to measure."""
    for ref in REFS:
        if git_run.run_git(["-C", str(repo), "rev-parse", "--verify", "-q", ref]).returncode != 0:
            continue
        if as_of:
            sha = _git(repo, "rev-list", "-1", "--first-parent", f"--before={as_of}", ref)
            if not sha:
                raise Unknown(f"{ref} has no commit at or before {as_of}")
        else:
            sha = _git(repo, "rev-parse", ref)
        return ref, sha, _git(repo, "show", "-s", "--format=%cI", sha)
    raise Unknown("no origin/main, origin/master or HEAD")


def read_tree(repo: Path, sha: str) -> dict[str, bytes]:
    """`{path: bytes}` for every regular tracked file at `sha` (symlinks and submodules skipped)."""
    res = git_run.run_git_bytes(["-C", str(repo), "archive", "--format=tar", sha], timeout=GIT_TIMEOUT_S)
    if res.returncode != 0:
        raise Unknown(f"git archive failed: {res.stderr.decode('utf-8', 'replace').strip()[:200]}")
    blobs: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(res.stdout)) as tar:
        for member in tar:
            if member.isfile():
                handle = tar.extractfile(member)
                blobs[member.name] = handle.read() if handle else b""
    return blobs


def _text(raw: bytes) -> Optional[str]:
    """The file as text, or None for a binary file (a NUL in its first 4 KiB)."""
    return None if b"\0" in raw[:4096] else raw.decode("utf-8", "replace")


def _hits(raw: Optional[bytes], table: str) -> Optional[int]:
    """One ratchet's baselined hits (0 for a missing baseline), or None when its baseline cannot be counted."""
    if raw is None:
        return 0
    try:
        return ratchet_reader.count_hits(raw.decode("utf-8", "replace"), ratchet_reader.RATCHETS[table])
    except structure_baseline.Unknown:
        return None


def ratchets(blobs: dict[str, bytes]) -> dict:
    """Enabled ratchet tables and their baselined hits, plus the structure baseline per kind.

    A baseline that cannot be counted is `None` in `hits` and named in `unknown`; the other
    tables still count. An unparseable `.fleet.toml` makes the whole reading `unknown`.
    """
    toml_raw = blobs.get(fleet_toml.FILENAME)
    toml_text = toml_raw.decode("utf-8", "replace") if toml_raw is not None else None
    try:
        config = ratchet_reader.read_config(toml_text)
    except structure_baseline.Unknown as exc:
        return {"state": "unknown", "reason": str(exc), "hits": {}, "unknown": [], "structure": None, "shape": None}
    hits = {table: _hits(blobs.get(path), table) for table, path in config["ratchets"].items()}
    structure = None
    if "structure_ratchet" in config["ratchets"]:
        raw = blobs.get(config["ratchets"]["structure_ratchet"])
        text = raw.decode("utf-8", "replace") if raw is not None else json.dumps({"version": 1, "entries": []})
        try:
            counts = structure_baseline.count_entries(text, FILE_LIMIT, FN_LIMITS[0])
            structure = {k: counts[k] for k in ("files", "fns", "dirs")}
        except structure_baseline.Unknown:
            structure = None
    parsed = fleet_toml.parse(toml_text) if toml_text else None
    shape = parsed.get("shape") if isinstance(parsed, dict) and isinstance(parsed.get("shape"), str) else None
    return {
        "state": "adopted" if hits else "not_adopted", "hits": hits,
        "unknown": sorted(t for t, n in hits.items() if n is None), "structure": structure, "shape": shape,
    }


def guess_shape(blobs: dict[str, bytes]) -> str:
    """A shape id for a repo that declares none in `.fleet.toml`, from what its code imports."""
    py = [t for p, raw in blobs.items() if p.endswith(".py") and measure.is_code(p) and (t := _text(raw))]
    if any("FastAPI(" in t for t in py):
        return "webapp-tray"
    if any("import streamlit" in t for t in py):
        return "streamlit"
    if not py and "package.json" in blobs:
        return "node"
    if not py and any(p.endswith(".html") for p in blobs):
        return "static-site"
    return "pipeline-cli"


def gate_checks(blobs: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """`(gate files, checks named in them)`, by the step-1 keyword match."""
    files = sorted(
        p for p in blobs
        if PurePosixPath(p).name in GATE_FILE_NAMES
        or (p.startswith(".github/workflows/") and p.count("/") == 2 and p.endswith((".yml", ".yaml")))
    )
    found = set()
    for path in files:
        text = (_text(blobs[path]) or "").lower()
        found.update(kw for kw in GATE_KEYWORDS if kw in text)
    return files, sorted(found)


def vendored(blobs: dict[str, bytes]) -> tuple[str, ...]:
    """The `dest` paths of the repo's `.fleet.toml` `[vendored]` table (empty when it has none or it does not parse)."""
    raw = blobs.get(fleet_toml.FILENAME)
    try:
        return measure.structure_ratchet.vendored_dests(raw.decode("utf-8", "replace")) if raw else ()
    except ValueError:
        return ()


def _is_vendored(path: str, dests: tuple[str, ...]) -> bool:
    return any(path == d or path.startswith(d + "/") for d in dests)


def _code_metrics(blobs: dict[str, bytes]) -> dict:
    """Lines, big files, function spans, flat directories and layer imports over the code files."""
    dests = vendored(blobs)
    lines: dict[str, int] = {}
    spans: list[tuple[int, str]] = []
    layer: list[str] = []
    unparsed: list[str] = []
    for path, raw in blobs.items():
        if not measure.is_code(path) or (text := _text(raw)) is None:
            continue
        lines[path] = measure.count_lines(text)
        found = measure.functions(path, text)
        if found is None:
            unparsed.append(path)
        else:
            spans += found
        layer += [f"{path} -> {module}" for module in measure.layer_imports(path, text)]
    ranked = sorted(lines.items(), key=lambda kv: (-kv[1], kv[0]))
    spans.sort(key=lambda s: (-s[0], s[1]))
    return {
        "loc": sum(lines.values()), "files": len(lines),
        "loc_vendored": sum(n for p, n in lines.items() if _is_vendored(p, dests)),
        # The near-limit bands count the repo's own code: a vendored copy was sized upstream.
        "files_near_limit": sum(1 for p, n in lines.items() if NEAR_FILE[0] <= n <= NEAR_FILE[1] and not _is_vendored(p, dests)),
        "fns_near_limit": sum(
            1 for n, where in spans
            if NEAR_FN[0] <= n <= NEAR_FN[1] and not _is_vendored(where.rsplit(":", 1)[0], dests)
        ),
        "largest": list(ranked[0]) if ranked else None,
        "over_1k": [[p, n, measure.kind(p)] for p, n in ranked if n > FILE_LIMIT],
        "fns_over": {str(limit): sum(1 for n, _ in spans if n > limit) for limit in FN_LIMITS},
        "fns_300": [[n, where] for n, where in spans if n >= FN_LIMITS[-1]],
        "longest_fns": [[n, where] for n, where in spans[:TOP_FUNCTIONS]],
        "flat_dirs": [[d, n] for d, n in measure.flat_dirs(sorted(lines))],
        "layer_imports": sorted(layer),
        "unparsed_python": sorted(unparsed),
    }


def census_repo(name: str, repo: Path, as_of: Optional[str]) -> dict:
    """One repo's census record: `status` `ok` with its metrics, or `unknown` with a reason."""
    if not (repo / ".git").exists():
        return {"repo": name, "status": "unknown", "reason": "no checkout"}
    try:
        ref, sha, when = resolve_ref(repo, as_of)
        blobs = read_tree(repo, sha)
    except Unknown as exc:
        return {"repo": name, "status": "unknown", "reason": str(exc)}
    gate_files, checks = gate_checks(blobs)
    exts = Counter(PurePosixPath(p).suffix.lower() for p in blobs if measure.is_code(p))
    ratchet = ratchets(blobs)
    declared = ratchet.get("shape")
    return {
        "repo": name, "status": "ok", "ref": ref, "sha": sha, "commit_time": when,
        "shape": declared or guess_shape(blobs), "shape_declared": bool(declared),
        "tracked_files": len(blobs), "code_ext": dict(exts.most_common()),
        **_code_metrics(blobs),
        "gate_files": gate_files, "gate_checks": checks, "ratchets": ratchet,
    }
