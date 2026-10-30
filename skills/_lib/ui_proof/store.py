"""The per-repo `.proof/` store: before/after captures, one folder per PR, an index, pruning.

Layout, rooted at the primary checkout so a worktree's proof outlives the worktree:

  .proof/_branch/<branch>/   captures taken before the PR number exists
  .proof/<PR>/               before-*.png, after-*.png, proof.json, index.html
  .proof/index.json          {"<PR>": {title, issue, merge_sha, utc, branch}}

`.proof/` is never tracked: it holds screenshots of a running app, and every
repo is treated as public.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import git_run
from fleet_core import hooks_state
from ui_proof import proof

DIR_NAME = ".proof"
BRANCH_DIR = "_branch"
INDEX_NAME = "index.json"
DATA_NAME = "proof.json"
PAGE_NAME = "index.html"
PHASES = ("before", "after")
LEG_ORDER = ("phone-light", "phone-dark", "desktop-light", "desktop-dark")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_out(repo: Path, *args: str) -> Optional[str]:
    res = git_run.run_git(["-C", str(repo), *args])
    return res.stdout.strip() if res.returncode == 0 else None


def proof_root(repo: Path) -> Optional[Path]:
    """`<primary checkout>/.proof`, found through the shared git dir so a worktree resolves to its primary."""
    common = _git_out(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent / DIR_NAME if common else None


def current_branch(repo: Path) -> Optional[str]:
    return _git_out(repo, "branch", "--show-current") or None


def safe_name(text: str) -> str:
    return _SAFE.sub("-", text).strip("-") or "unnamed"


def tracked_paths(repo: Path) -> Optional[List[str]]:
    """Tracked files under `.proof/`; `None` when git could not answer."""
    out = _git_out(repo, "ls-files", "--", DIR_NAME)
    return None if out is None else [p for p in out.splitlines() if p.strip()]


def is_ignored(repo: Path) -> Optional[bool]:
    """Whether `.proof/` is gitignored in `repo`; `None` when git could not answer."""
    res = git_run.run_git(["-C", str(repo), "check-ignore", "-q", "--no-index", f"{DIR_NAME}/probe.png"])
    return {0: True, 1: False}.get(res.returncode)


def guard_lines(repo: Path) -> List[str]:
    """What is wrong with `.proof/` hygiene in `repo`, or nothing."""
    tracked, ignored = tracked_paths(repo), is_ignored(repo)
    need = []
    if tracked is None or ignored is None:
        need.append(".proof/ hygiene could not be checked (git ls-files/check-ignore failed)")
    if tracked:
        need.append(f"{DIR_NAME}/ is tracked ({', '.join(tracked[:5])}); `git rm -r --cached {DIR_NAME}`")
    if ignored is False:
        need.append(f"{DIR_NAME}/ is not gitignored; add a `{DIR_NAME}/` line to .gitignore")
    return need


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    hooks_state.atomic_write_text(path, json.dumps(data, indent=2))


def parse_images(pairs: List[str]) -> List[dict]:
    """`["<state>:<viewport>-<theme>=<png>", ...]` -> manifest-shaped entries without lint findings."""
    entries = []
    for pair in pairs:
        head, sep, path = pair.partition("=")
        state, sep2, leg = head.rpartition(":")
        viewport, sep3, theme = leg.partition("-")
        if not (sep and sep2 and sep3 and state and viewport and theme and path):
            raise ValueError(f"--image needs '<state>:<viewport>-<theme>=<png>', got {pair!r}")
        entries.append({"state": state, "viewport": viewport, "theme": theme, "screenshot": path})
    return entries


def capture(repo: Path, phase: str, manifest: Optional[Path], images: List[dict]) -> Tuple[bool, str]:
    """Copy one phase's screenshots into this branch's staging folder; `(ok, CAPTURED=... line)`."""
    root, branch = proof_root(repo), current_branch(repo)
    if root is None or branch is None:
        return False, "CAPTURED=none reason=not a git checkout on a named branch"
    entries: List[dict] = list(images)
    linted = manifest is not None
    if manifest is not None:
        loaded = proof.load_manifest(manifest)
        if loaded is None:
            return False, f"CAPTURED=none reason=no readable manifest at {manifest}"
        entries += loaded
    if not entries:
        return False, "CAPTURED=none reason=give --manifest and/or --image"
    folder = root / BRANCH_DIR / safe_name(branch)
    folder.mkdir(parents=True, exist_ok=True)
    data = _load_json(folder / DATA_NAME)
    legs: Dict[str, dict] = {_leg_key(e): e for e in data.get(phase, [])}
    missing = []
    for e in entries:
        name = f"{phase}-{safe_name(str(e.get('state')))}-{e.get('viewport')}-{e.get('theme')}.png"
        try:
            shutil.copyfile(str(e.get("screenshot", "")), folder / name)
        except OSError:
            missing.append(_leg_key(e))
            continue
        row = {"state": e.get("state"), "viewport": e.get("viewport"), "theme": e.get("theme"), "file": name}
        if linted and "findings" in e:
            row["findings"] = e.get("findings") or []
        legs[_leg_key(row)] = row
    data.update({"branch": branch, phase: list(legs.values()), f"{phase}_utc": utc_now()})
    _write_json(folder / DATA_NAME, data)
    line = f"CAPTURED={phase} legs={len(entries) - len(missing)} folder={folder}"
    if missing:
        line += " missing_screenshots=" + ",".join(missing)
    return not missing, line


def _leg_key(entry: dict) -> str:
    return f"{entry.get('state')} {entry.get('viewport')}-{entry.get('theme')}"


def _merge_into(src: Path, dst: Path) -> None:
    """Move staging captures into the PR folder; a re-capture overwrites the same leg."""
    dst.mkdir(parents=True, exist_ok=True)
    incoming, existing = _load_json(src / DATA_NAME), _load_json(dst / DATA_NAME)
    for item in src.iterdir():
        if item.name != DATA_NAME:
            shutil.move(str(item), str(dst / item.name))
    for phase in PHASES:
        rows = {_leg_key(e): e for e in existing.get(phase, [])}
        rows.update({_leg_key(e): e for e in incoming.get(phase, [])})
        existing[phase] = list(rows.values())
        if incoming.get(f"{phase}_utc"):
            existing[f"{phase}_utc"] = incoming[f"{phase}_utc"]
    existing["branch"] = incoming.get("branch") or existing.get("branch")
    _write_json(dst / DATA_NAME, existing)
    shutil.rmtree(src)


def prepare_pr_folder(repo: Path, pr: int) -> Optional[Path]:
    """`.proof/<PR>/`, absorbing this branch's staging captures first."""
    root = proof_root(repo)
    if root is None:
        return None
    folder = root / str(pr)
    branch = current_branch(repo)
    staging = root / BRANCH_DIR / safe_name(branch) if branch else None
    if staging is not None and staging.is_dir():
        _merge_into(staging, folder)
    return folder


def load_data(folder: Path) -> dict:
    return _load_json(folder / DATA_NAME)


def save_data(folder: Path, data: dict) -> None:
    _write_json(folder / DATA_NAME, data)


def update_index(root: Path, pr: int, row: dict) -> None:
    index = _load_json(root / INDEX_NAME)
    index[str(pr)] = {**(index.get(str(pr)) or {}), **{k: v for k, v in row.items() if v not in (None, "")}}
    _write_json(root / INDEX_NAME, index)


def page_exists(repo: Path, pr: int) -> bool:
    root = proof_root(repo)
    return root is not None and (root / str(pr) / PAGE_NAME).is_file()


def parse_age(text: str) -> timedelta:
    """`30d`, `12h` or `90m` -> a timedelta; raises ValueError otherwise."""
    m = re.fullmatch(r"(\d+)([dhm])", text.strip())
    if not m:
        raise ValueError(f"--older-than needs <N>d, <N>h or <N>m, got {text!r}")
    n = int(m.group(1))
    return {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[m.group(2)]


def _age_utc(folder: Path, index_row: Optional[dict]) -> datetime:
    """The newest of the folder's mtime and its index stamp: a rebuilt proof is young again."""
    stamps = [datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc)]
    utc = (index_row or {}).get("utc")
    if isinstance(utc, str):
        try:
            stamps.append(datetime.strptime(utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
        except ValueError:
            pass
    return max(stamps)


def prune(repo: Path, older_than: timedelta, now: Optional[float] = None) -> List[str]:
    """Remove PR and staging folders older than `older_than`, and their index rows; nothing else."""
    root = proof_root(repo)
    if root is None:
        return ["PRUNED=0 reason=not a git checkout"]
    if not root.is_dir():
        return ["PRUNED=0"]
    cutoff = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc) - older_than
    index = _load_json(root / INDEX_NAME)
    candidates = [d for d in root.iterdir() if d.is_dir() and d.name.isdigit()]
    staging = root / BRANCH_DIR
    if staging.is_dir():
        candidates += [d for d in staging.iterdir() if d.is_dir()]
    removed = []
    for folder in candidates:
        row = index.get(folder.name) if folder.parent == root else None
        if _age_utc(folder, row) < cutoff:
            shutil.rmtree(folder)
            removed.append(folder.relative_to(root).as_posix())
            if folder.parent == root:
                index.pop(folder.name, None)
    if removed:
        _write_json(root / INDEX_NAME, index)
    return [f"PRUNED={len(removed)}"] + [f"REMOVED={r}" for r in removed]
