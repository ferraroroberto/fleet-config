"""The routing report (step 2): merged PRs routed through the repo's own classifier.

Part of the `e2e_value` package; see its `__init__` for the reports and their shapes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from typing import Dict, List, Optional, Sequence, Tuple

import git_run

from .sources import GH_FILES_TIMEOUT_S, GIT_TIMEOUT_S, repo_slug, _test_tree_files

__all__ = [
    "load_classifier",
    "merged_prs",
    "pr_sheet_changes",
    "sheet_bucket",
    "shadow_entry",
    "classify_cmd",
    "import_holes",
    "routing_report",
]


# ---- routing report (step 2) ----------------------------------------------------------------


def load_classifier(repo_root: Path):
    """The repo's own `scripts/classify_e2e.py`, imported read-only, or None.

    Imported, never reimplemented, so the report cannot drift from what the
    gate actually does (the file is byte-verbatim from project-scaffolding).
    """
    path = repo_root / "scripts" / "classify_e2e.py"
    if not path.is_file():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"classify_e2e_{abs(hash(str(path)))}", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve their module by name
    try:
        spec.loader.exec_module(mod)
    except Exception:  # a broken classifier is `unknown`, reported by the caller
        sys.modules.pop(spec.name, None)
        return None
    return mod if hasattr(mod, "classify") and hasattr(mod, "load_config") else None


def merged_prs(repo_root: Path, prs: int, until: Optional[str] = None,
               with_merge_commit: bool = False) -> Tuple[Optional[List[Dict[str, object]]], str]:
    """`(prs newest first, status)`: number, mergedAt and file paths of merged PRs.

    `with_merge_commit` adds each PR's `mergeCommit` sha ("" when gh has none),
    which the stylesheet routing reads blobs from.
    """
    slug = repo_slug(repo_root)
    if slug is None:
        return None, "unknown: no GitHub origin remote"
    limit = prs + (200 if until else 0)
    res = git_run.run_gh(["pr", "list", "--state", "merged", "--limit", str(limit), "--repo", slug,
                          "--json", "number,mergedAt,files" + (",mergeCommit" if with_merge_commit else "")],
                         timeout=GH_FILES_TIMEOUT_S, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        return None, "unknown: " + ((res.stderr or "").strip().splitlines() or ["gh failed"])[0][:160]
    try:
        items = json.loads(res.stdout or "[]")
    except ValueError:
        return None, "unknown: unparsable gh output"
    items.sort(key=lambda it: str(it.get("mergedAt") or ""), reverse=True)
    if until:
        items = [it for it in items if str(it.get("mergedAt") or "") <= until]
    items = items[:prs]
    out = [{"number": it["number"], "mergedAt": it.get("mergedAt"),
            "files": [f["path"] for f in (it.get("files") or []) if f.get("path")]} for it in items]
    if with_merge_commit:
        for row, it in zip(out, items):
            row["mergeCommit"] = str((it.get("mergeCommit") or {}).get("oid") or "")
    return out, f"ok ({len(out)} PRs)"


def _blob(repo_root: Path, rev: str, path: str) -> Tuple[bool, Optional[str]]:
    """`(exists, text)` of `path` at `rev`: `(False, None)` only when the commit is
    in the clone and the path is not in it; `(True, None)` when it could not be read."""
    res = git_run.run_git(["-C", str(repo_root), "show", f"{rev}:{path}"], timeout=GIT_TIMEOUT_S)
    if res.returncode == 0:
        return True, res.stdout
    commit = git_run.run_git(["-C", str(repo_root), "cat-file", "-e", f"{rev}^{{commit}}"], timeout=GIT_TIMEOUT_S)
    return (commit.returncode != 0), None


def pr_sheet_changes(mod, repo_root: Path, sha: str,
                     sheets: Sequence[str]) -> Tuple[Dict[str, object], List[str]]:
    """`(changed_selectors() per declared sheet the PR touched, the sheets that could not be read)`.

    Merge commit vs its first parent, mirroring the classifier's own
    `sheet_changes_from_git`: a sheet new in the PR diffs against the empty
    text. No merge commit, an object missing from the clone, or a deleted sheet
    gives None -- the whole suite, never a guess -- and is listed as unreadable,
    so it is never counted as an unsafe CSS change.
    """
    out: Dict[str, object] = {}
    unreadable: List[str] = []
    for sheet in sheets:
        if not sha:
            out[sheet] = None
            unreadable.append(sheet)
            continue
        old_exists, old = _blob(repo_root, f"{sha}^1", sheet)
        _new_exists, new = _blob(repo_root, sha, sheet)
        if not old_exists and new is not None:
            old = ""
        if old is None or new is None:
            unreadable.append(sheet)
        out[sheet] = mod.changed_selectors(old, new)
    return out, unreadable


def sheet_bucket(sels: object, surfaces: Sequence[object]) -> str:
    """Why one PR's change to a shared sheet does or doesn't narrow (fleet-config#1033)."""
    if sels is None:
        return "unsafe"
    if not sels:
        return "no rule changed"
    owners = set()
    for sel in sels:  # type: ignore[attr-defined]
        hits = [s for s in surfaces if s.owns_selector(sel)]  # type: ignore[attr-defined]
        if not hits:
            return "unmapped selector"
        if len(hits) > 1:
            return "spans surfaces"
        owners.add(getattr(hits[0], "name", id(hits[0])))
    return "owned by one surface" if len(owners) == 1 else "spans surfaces"


def _routes_sheets(mod) -> bool:
    """Whether this classifier can narrow a shared stylesheet (project-scaffolding#289)."""
    import inspect
    if not hasattr(mod, "changed_selectors"):
        return False
    try:
        return len(inspect.signature(mod.classify).parameters) >= 3
    except (TypeError, ValueError):
        return False


def _more_specific(later, first) -> bool:
    """Whether a later rule names a path more narrowly than the one that took it.

    An exact path always does; a pure extension rule (`*.md`, no prefix) does
    against a prefix rule, since it names a file type the prefix never meant;
    between two prefixes, the longer one does.
    """
    if later.path is not None:
        return first.path is None
    if later.prefix is None:
        return bool(later.extensions) and first.prefix is not None
    return first.prefix is not None and len(later.prefix) > len(first.prefix)


def shadow_entry(path: str, rules: list) -> Optional[Dict[str, object]]:
    """A shadowed path's lower-tier rules plus the rule that wins once the first one stops matching.

    The first-match-wins table can route a README full because a broad prefix
    rule sits above the docs rule (app-launcher#1220 (b)). `shadowed` lists
    the later, lower-tier, more specific rules that also match; a general rule
    placed after a specific one (`tests/` after `tests/e2e/`) is the intended
    order and is not reported. Dropping the path from the first rule hands it to `hits[1]`, which can be
    a broad full rule in between rather than the shadowed one:
    home-automation's vendored READMEs went to `app/webapp/`, not `*.md`
    (fleet-config#1134). `drop_safe` is true only when `hits[1]` is itself a
    shadowed rule; otherwise the fix is an explicit rule, checked with
    `classify_cmd` on the candidate table.
    """
    name = path.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    hits = [r for r in rules if r.matches(path, ext)]
    if len(hits) < 2:
        return None
    first = hits[0]
    shadowed = [r for r in hits[1:] if r.tier < first.tier and _more_specific(r, first)]
    if not shadowed:
        return None
    nxt = hits[1]
    return {"shadowed": [r.label for r in shadowed], "next_rule": nxt.label, "next_tier": _tier_name(nxt.tier),
            "drop_safe": nxt in shadowed}


def _tier_name(tier: object) -> object:
    """A rule tier as the classifier names it (`FULL`), or the raw value for a plain int."""
    return getattr(tier, "name", tier)


def classify_cmd(paths: Sequence[str]) -> str:
    """The classifier invocation that routes exactly these paths: a proposed rule's check, in seconds."""
    return "python scripts/classify_e2e.py " + " ".join(paths)


def _repo_file(repo_root: Path, dotted: str) -> Optional[str]:
    """The repo-relative `.py` a dotted module name resolves to (`a/b.py` or `a/b/__init__.py`), or None."""
    rel = dotted.replace(".", "/")
    for cand in (f"{rel}.py", f"{rel}/__init__.py"):
        if (repo_root / cand).is_file():
            return cand
    return None


_ROUTING_SOURCES = (".fleet.toml", "scripts/classify_e2e.py")


def _tracked_files(repo_root: Path) -> Optional[set]:
    """Repo-relative paths git tracks, or None when git gives no answer (not a repo, git missing): then nothing is filtered."""
    res = git_run.run_git(["-C", str(repo_root), "ls-files", "-z"], timeout=GIT_TIMEOUT_S)
    if res.returncode != 0:
        return None
    return {p for p in res.stdout.split("\0") if p}


def _read_files(parsed, repo_root: Path) -> set:
    """Repo files (not `.py`) a parsed module names as a repo-relative path: `'a/b.json'` or `ROOT / 'a' / 'b.json'`.

    Only a path that exists under the repo counts, so a stray string or a path
    built from a variable is skipped, never guessed (facilitation-suite#165).
    """
    import ast

    def parts(n) -> List[str]:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            return parts(n.left) + parts(n.right)
        return [n.value] if isinstance(n, ast.Constant) and isinstance(n.value, str) else []

    cands = set()
    for n in ast.walk(parsed):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            cands.add(n.value)
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            cands.add("/".join(p.strip("/") for p in parts(n)))
    root = repo_root.resolve()
    out = set()
    for c in cands:
        c = c.replace("\\", "/")
        if not c or len(c) > 200 or "\n" in c or c.startswith("/") or ".." in c.split("/") or c.endswith(".py"):
            continue
        if PureWindowsPath(c).anchor:  # `C:/Windows/Fonts/x.ttf`: `root / c` would drop the root and leave the repo
            continue
        p = (root / c).resolve()
        if "." in c.rsplit("/", 1)[-1] and p.is_file() and root in p.parents:
            out.add(p.relative_to(root).as_posix())
    return out


def _module_hits(parsed, repo_root: Path) -> set:
    """Repo `.py` files a parsed module imports, absolute imports at any depth (a function body included)."""
    import ast
    seen: set = set()
    for n in ast.walk(parsed):
        names: List[str] = []
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            names = [n.module] + [f"{n.module}.{a.name}" for a in n.names]
        for name in names:
            hit = _repo_file(repo_root, name)
            if hit:
                seen.add(hit)
    return seen


def import_holes(repo_root: Path, mod, config, test_dirs: Sequence[str]) -> List[Dict[str, object]]:
    """Files the e2e suite loads or imports that the routing table sends to `none` (task-os#287).

    A diff touching only such a file runs no browser suite, though 12 of
    task-os's e2e modules imported `tests/fixtures/*.py` and `tests/conftest.py`.
    The `routing` scan lists paths from merged PRs, so a file nobody changed
    lately never showed. This reads the other direction: every module under
    the test dirs, the conftest and the `_*.py` plugins (`kind: loaded`), plus
    every repo file those modules import (`kind: imported`, `imported_by`
    counting the importers), plus every non-Python repo file those modules name
    as a repo-relative path (`kind: read`, tracked files only, `imported_by` counting the readers:
    facilitation-suite's conftest read `config/config.sample.json` for every
    instance while the table routed `config/` to `none`), each routed through
    the repo's own classifier. The files that define routing itself (`.fleet.toml`
    and `scripts/classify_e2e.py`, the classifier's own `_ROUTING_SOURCES`) are
    listed too (`kind: routing-source`): a diff editing only the table reroutes
    the suite without running it (facilitation-suite#165). A test-support helper (a `.py` under a `tests/` directory) is followed through its own imports, function bodies included, to any depth (parking-manager#58: `tests/mockup_seed.py` imported `tests/burst_fixture.py` inside a function); app source is never walked, and absolute imports only. Backend source
    the suite boots (`src/*.py` routed `none`) lands here too; whether that
    is a hole or a deliberate gate-time trade is the owner's call.
    """
    import ast
    tree = _test_tree_files(repo_root, test_dirs)
    loaded = {str(p.relative_to(repo_root)).replace("\\", "/") for p in tree}
    imported: Dict[str, int] = {}
    read: set = set()
    tracked = _tracked_files(repo_root)  # a gitignored local file a test reads is never in a PR diff
    for p in tree:
        try:
            parsed = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        seen = _module_hits(parsed, repo_root)
        files = {f for f in _read_files(parsed, repo_root) if tracked is None or f in tracked}
        read |= files - seen
        for hit in seen | files:
            imported[hit] = imported.get(hit, 0) + 1
    # A test-support helper (a `.py` under a `tests/` directory) is part of the suite: follow what it imports, to any depth
    # (parking-manager#58: `mockup_seed.seed_live` imports `tests.burst_fixture` in its body). App source is never walked.
    queue = [f for f in imported if "tests" in f.split("/")[:-1] and f.endswith(".py")]
    walked: set = set()
    while queue:
        helper = queue.pop()
        if helper in walked:
            continue
        walked.add(helper)
        try:
            parsed = ast.parse((repo_root / helper).read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for hit in _module_hits(parsed, repo_root) - {helper}:
            imported[hit] = imported.get(hit, 0) + 1
            if "tests" in hit.split("/")[:-1]:
                queue.append(hit)
    sources = {p for p in getattr(mod, "_ROUTING_SOURCES", _ROUTING_SOURCES) if (repo_root / p).is_file()}
    out = []
    for path in sorted(loaded | set(imported) | sources):
        cat, label = mod._classify_one(path, config.rules)
        if getattr(cat, "name", cat) != "NONE":
            continue
        out.append({"path": path, "rule": label, "kind": "loaded" if path in loaded else "read" if path in read else "imported" if path in imported else "routing-source",
                    "imported_by": imported.get(path, 0), "check": classify_cmd([path])})
    return sorted(out, key=lambda e: (-int(e["imported_by"]), str(e["path"])))  # type: ignore[call-overload]


def _counterfactual(repo_root: Path, mod, proposed, test_dirs: Sequence[str], path: str, narrowed: List[Dict[str, object]],
                    holes: List[Dict[str, object]], unclassified: List[Dict[str, object]],
                    shadowed: List[Dict[str, object]]) -> Dict[str, object]:
    """What a candidate table changes: PR tiers, plus the findings re-run against it (facilitation-suite#165).

    `unclassified`, `shadowed` and `import_holes` are the declared table's
    findings recomputed with the candidate's rules, and `holes_opened` /
    `holes_closed` are the import-hole paths that appear or disappear, so a
    routing proposal's effect on coverage is read here rather than proved by
    swapping the file in and classifying paths by hand.
    """
    after = import_holes(repo_root, mod, proposed, test_dirs)
    before_paths = {str(h["path"]) for h in holes}
    after_paths = {str(h["path"]) for h in after}
    return {"proposed": path, "changed": narrowed, "unclassified": unclassified, "shadowed": shadowed, "import_holes": after,
            "holes_opened": sorted(after_paths - before_paths), "holes_closed": sorted(before_paths - after_paths)}


def _note_paths(files: Sequence[str], labels: Sequence[str], rules: list,
                unclassified: Dict[str, int], shadowed: Dict[str, Dict[str, object]]) -> None:
    """Tally one PR's unclassified and shadowed paths under `rules` (labels are what each path matched)."""
    for f, lab in zip(files, labels):
        if lab == "unclassified":
            unclassified[f] = unclassified.get(f, 0) + 1
        sh = shadow_entry(f.replace("\\", "/"), rules)
        if sh:
            entry = shadowed.setdefault(f, {"path": f, "took": lab, **sh, "check": classify_cmd([f]), "prs": 0})
            entry["prs"] = int(entry["prs"]) + 1  # type: ignore[call-overload]


def _unclassified_rows(unclassified: Dict[str, int]) -> List[Dict[str, object]]:
    return [{"path": p, "prs": n, "check": classify_cmd([p])} for p, n in sorted(unclassified.items(), key=lambda kv: -kv[1])]


def _shadowed_rows(shadowed: Dict[str, Dict[str, object]]) -> List[Dict[str, object]]:
    return sorted(shadowed.values(), key=lambda e: -int(e["prs"]))  # type: ignore[arg-type,call-overload]


def routing_report(repo_root: Path, prs: int = 60, until: Optional[str] = None,
                   config_path: Optional[Path] = None, proposed_path: Optional[Path] = None,
                   pr_list: Optional[List[Dict[str, object]]] = None,
                   test_dirs: Sequence[str] = ("tests/e2e",)) -> Dict[str, object]:
    mod = load_classifier(repo_root)
    if mod is None:
        return {"status": "unknown", "reason": "no importable scripts/classify_e2e.py in the repo"}
    config = mod.load_config(config_path or (repo_root / ".fleet.toml"))
    proposed = mod.load_config(proposed_path) if proposed_path else None
    declared = sorted(set(getattr(config, "shared_stylesheets", ()) or ())
                      | set(getattr(proposed, "shared_stylesheets", ()) or ()))
    by_sheet = bool(declared) and _routes_sheets(mod)
    if pr_list is None:
        pr_list, why = merged_prs(repo_root, prs, until, with_merge_commit=by_sheet)
        if pr_list is None:
            return {"status": "unknown", "reason": f"merged PRs: {why}"}
    sheet_reasons: Dict[str, Dict[str, int]] = {}
    tiers: Dict[str, int] = {}
    full_classes: Dict[str, int] = {}
    single_cause: Dict[str, int] = {}
    full_paths: Dict[str, int] = {}
    unclassified: Dict[str, int] = {}
    shadowed: Dict[str, Dict[str, object]] = {}
    p_unclassified: Dict[str, int] = {}
    p_shadowed: Dict[str, Dict[str, object]] = {}
    narrowed: List[Dict[str, object]] = []
    rows = []
    browser_relevant = 0
    unreadable_prs = 0
    for pr in pr_list:
        files = [str(f) for f in pr["files"]]  # type: ignore[union-attr]
        if not files:
            # GitHub answers 422 for a diff it cannot generate and `gh` returns `files: []`; `classify([])` fails
            # safe to `full`, which counted every such PR as a full-tier one. No file list is no evidence of a tier
            # -- its own count, in no tier and no counterfactual (fleet-config#1138).
            unreadable_prs += 1
            continue
        slashed = {f.replace("\\", "/") for f in files}
        touched = [sheet for sheet in declared if sheet in slashed] if by_sheet else []
        changes, unreadable = (pr_sheet_changes(mod, repo_root, str(pr.get("mergeCommit") or ""), touched)
                               if touched else ({}, []))
        extra = (changes,) if by_sheet else ()
        r = mod.classify(files, config, *extra)
        for sheet, sels in changes.items():
            bucket = "unreadable" if sheet in unreadable else sheet_bucket(sels, (proposed or config).surfaces)
            counts = sheet_reasons.setdefault(sheet, {})
            counts[bucket] = counts.get(bucket, 0) + 1
        tiers[r.tier] = tiers.get(r.tier, 0) + 1
        cats = [(f, *mod._classify_one(f.replace("\\", "/"), config.rules)) for f in files]
        if any(c.name != "NONE" for _, c, _ in cats):
            browser_relevant += 1
        if r.tier == "full":
            labels = sorted({lab for _, c, lab in cats if c.name == "FULL"})
            for lab in labels:
                full_classes[lab] = full_classes.get(lab, 0) + 1
            if len(labels) == 1:
                single_cause[labels[0]] = single_cause.get(labels[0], 0) + 1
            for f, c, _ in cats:
                if c.name == "FULL":
                    full_paths[f] = full_paths.get(f, 0) + 1
        _note_paths(files, [lab for _, _, lab in cats], config.rules, unclassified, shadowed)
        if proposed is not None:
            _note_paths(files, [mod._classify_one(f.replace("\\", "/"), proposed.rules)[1] for f in files],
                        proposed.rules, p_unclassified, p_shadowed)
            pr_ = mod.classify(files, proposed, *extra)
            if pr_.tier != r.tier:
                narrowed.append({"pr": pr["number"], "from": r.tier, "to": pr_.tier, "surface": pr_.surface})
        rows.append({"pr": pr["number"], "tier": r.tier, "surface": r.surface})
    full_n = tiers.get("full", 0)
    holes = import_holes(repo_root, mod, config, test_dirs)
    from e2e_route import gate_contract
    gate, gate_reason, readers = gate_contract(repo_root)
    return {
        "status": "ok",
        "prs": len(pr_list),
        # PRs `gh` returned with no file list: counted in `prs`, in no tier below (fleet-config#1138).
        "prs_unreadable": unreadable_prs,
        # Whether the gate runs this routing at all: a `not-consumed` table saves the gate nothing (fleet-config#1134).
        "gate": {"verdict": gate, "reason": gate_reason, "readers": [{"file": f, "split": sp} for f, sp in readers]},
        "config": str(config_path or (repo_root / ".fleet.toml")), "config_source": config.source,
        "tiers": {k: tiers.get(k, 0) for k in ("skip", "static", "surface", "full")},
        "browser_relevant": browser_relevant,
        "browser_relevant_full": full_n,
        "full_classes": dict(sorted(full_classes.items(), key=lambda kv: -kv[1])),
        "single_cause": dict(sorted(single_cause.items(), key=lambda kv: -kv[1])),
        "full_paths_top": [{"path": p, "prs": n} for p, n in sorted(full_paths.items(), key=lambda kv: -kv[1])[:10]],
        "unclassified": _unclassified_rows(unclassified),
        "shadowed": _shadowed_rows(shadowed),
        "import_holes": holes,
        "counterfactual": None if proposed is None else _counterfactual(
            repo_root, mod, proposed, test_dirs, str(proposed_path), narrowed, holes,
            _unclassified_rows(p_unclassified), _shadowed_rows(p_shadowed)),
        "sheet_routing": ("n/a: no shared_stylesheets declared" if not declared
                          else "n/a: classifier routes file lists only" if not by_sheet
                          else {"sheets": declared, "reasons": sheet_reasons}),
        "per_pr": rows,
    }
