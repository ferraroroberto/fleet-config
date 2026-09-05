"""Reconcile per-skill discovery links without copying or replacing sources.

The project-scaffolding docs/agents/project-skills.md contract owns the layout.
Filesystem diagnostics explicitly do not claim that a native client loaded it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fleet_repo_scan import fleet_repos
from frontmatter import frontmatter_field
from git_run import run_git
from no_window import NO_WINDOW

MANIFEST = ".fleet-config-discovery.json"
OWNER = "fleet-config/scoped-discovery-v1"
ROOTS = (".claude", ".agents")
EXCLUDED = {"node_modules", "conversations", "memory", "logs", "dist", "build"}


def is_link(path: Path) -> bool:
    """Recognize dangling symlinks and Windows junctions without following them."""
    return path.is_symlink() or path.is_junction()


def present(path: Path) -> bool:
    """Include dangling links in collision checks."""
    return path.exists() or is_link(path)


@dataclass(frozen=True)
class Skill:
    scope: Path
    path: Path
    name: str

    @property
    def source(self) -> Path:
        return self.path.resolve()


def skill_at(scope: Path, path: Path) -> Optional[Skill]:
    """Read the small discovery header, never publish helper containers."""
    if path.name.startswith(("_", ".")) or not (path / "SKILL.md").is_file():
        return None
    text = (path / "SKILL.md").read_text(encoding="utf-8-sig")
    parts = re.split(r"^---\s*$", text, flags=re.MULTILINE, maxsplit=2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError(f"invalid frontmatter: {path / 'SKILL.md'}")
    name = (frontmatter_field(text, "name") or "").strip("\"'")
    description = frontmatter_field(text, "description")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or not description:
        raise ValueError(f"invalid name/description: {path / 'SKILL.md'}")
    return Skill(scope, path, name)


def skills_in(scope: Path) -> list[Skill]:
    """Inventory both discovery routes at one scope, including real directories."""
    result = []
    for root in ROOTS:
        folder = scope / root / "skills"
        if folder.is_dir():
            for entry in sorted(folder.iterdir()):
                skill = skill_at(scope, entry)
                if skill:
                    result.append(skill)
    return result


def scopes_in(repo: Path) -> list[Path]:
    """Walk real package directories only, never private agent/runtime trees."""
    scopes = []
    def failed(error: OSError) -> None:
        raise error

    for folder, dirs, _files in os.walk(repo, followlinks=False, onerror=failed):
        scope = Path(folder)
        if (any((scope / root / "skills").is_dir() for root in ROOTS)
                or any((scope / name).is_file() for name in ("AGENTS.md", "CLAUDE.md"))):
            scopes.append(scope)
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_"))
                         and d not in EXCLUDED and not is_link(scope / d)
                         and not (scope / d / ".git").exists())
    return scopes


def load_manifest(repo: Path) -> dict:
    """Refuse corrupt/foreign state instead of claiming unknown links."""
    path = manifest_path(repo)
    if not present(path):
        return {"owner": OWNER, "links": {}}
    if is_link(path):
        raise ValueError(f"manifest is a link: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("owner") != OWNER or not isinstance(data.get("links"), dict):
        raise ValueError(f"unknown manifest owner/schema: {path}")
    for key, source in data["links"].items():
        target = repo / key
        if (Path(key).anchor or not target.is_relative_to(repo) or ".." in Path(key).parts
                or target.name.startswith((".", "_"))
                or target.parent.name != "skills"
                or target.parent.parent.name not in ROOTS
                or not isinstance(source, str) or not Path(source).is_absolute()):
            raise ValueError(f"unsafe manifest entry: {key}")
    block = data.get("exclude_block", "")
    if block:
        identity = data.get("exclude_id", "")
        if (not isinstance(block, str) or not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{16}", identity)
                or not block.startswith(f"\n# {OWNER} {identity} BEGIN\n")
                or not block.endswith(f"# {OWNER} {identity} END\n")):
            raise ValueError("invalid owned exclude block")
        result = run_git(["-C", str(repo), "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], check=True)
        exclude = Path(result.stdout.strip())
        if is_link(exclude) or not exclude.is_file() or exclude.read_bytes().count(block.encode()) != 1:
            raise ValueError(f"owned Git exclude block changed; preserve and inspect: {exclude}")
    return data


def manifest_path(repo: Path) -> Path:
    """Keep ownership in checkout-specific Git metadata, never private tracked prose."""
    git_dir = repo / ".git"
    if git_dir.is_file():
        pointer = git_dir.read_text(encoding="utf-8").strip()
        if not pointer.startswith("gitdir: "):
            raise ValueError(f"invalid worktree gitdir pointer: {git_dir}")
        git_dir = (repo / pointer.removeprefix("gitdir: ")).resolve()
    if not git_dir.is_dir():
        raise ValueError(f"missing checkout metadata directory: {git_dir}")
    return git_dir / MANIFEST


def _persist(repo: Path, manifest: dict) -> None:
    path = manifest_path(repo)
    temporary = path.with_suffix(".json.tmp")
    if present(temporary):
        raise ValueError(f"manifest temporary path occupied: {temporary}")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
    temporary.replace(path)


def _save(repo: Path, manifest: dict) -> None:
    """Own exact ignore routes, with separate blocks for shared-Git worktrees."""
    old = manifest.get("exclude_block", "")
    if not old and not manifest["links"]:
        _persist(repo, manifest)
        return
    result = run_git(["-C", str(repo), "rev-parse", "--path-format=absolute", "--git-path", "info/exclude"], check=True)
    exclude = Path(result.stdout.strip())
    if is_link(exclude):
        raise ValueError(f"Git exclude file is a link; preserve and inspect: {exclude}")
    exclude.parent.mkdir(parents=True, exist_ok=True)
    lock = exclude.with_name(".fleet-discovery-exclude.lock")
    # Shared info/exclude is edited under a short exclusive lock. A simultaneous
    # reconciliation reports unknown/retry, never loses a sibling's block.
    with lock.open("x") as guard:
        try:
            content = exclude.read_bytes() if exclude.exists() else b""
            identity = manifest.get("exclude_id", hashlib.sha256(str(repo).encode()).hexdigest()[:16])
            if not re.fullmatch(r"[a-f0-9]{16}", identity):
                raise ValueError("invalid exclude owner id")
            begin = f"\n# {OWNER} {identity} BEGIN\n"
            end = f"# {OWNER} {identity} END\n"
            if old and (not old.startswith(begin) or not old.endswith(end)):
                raise ValueError("invalid owned exclude block")
            if (old and content.count(old.encode()) != 1) or (not old and begin.encode() in content):
                raise ValueError(f"owned Git exclude block changed; preserve and inspect: {exclude}")
            patterns = ["/" + re.sub(r"([\\*?\[\]!#])", r"\\\1", key) + "/\n"
                        for key in sorted(manifest["links"])]
            new = begin + "".join(patterns) + end if patterns else ""
            updated = content.replace(old.encode(), new.encode(), 1) if old else content + new.encode()
            # Record ownership before the metadata write; interruption leaves an
            # explicit mismatch, never an unowned block silently left behind.
            manifest.update(exclude_id=identity, exclude_block=new)
            _persist(repo, manifest)
            if updated != content:
                temporary = exclude.with_name(".fleet-discovery-exclude.tmp")
                with temporary.open("xb") as stream:
                    stream.write(updated)
                temporary.replace(exclude)
        finally:
            # Close the lock handle before unlinking on Windows.
            guard.close()
            lock.unlink()


def _unlink(path: Path) -> None:
    # No recursive removal: a junction target may be the only maintained source.
    if path.is_junction():
        os.rmdir(path)
    else:
        path.unlink()


def _link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        # PowerShell reads paths from env, never interpolated as executable code.
        env = dict(os.environ, FLEET_LINK_SOURCE=str(source), FLEET_LINK_TARGET=str(target))
        subprocess.run(
            [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
             "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path $env:FLEET_LINK_TARGET -Target $env:FLEET_LINK_SOURCE | Out-Null"],
            check=True, env=env, capture_output=True, timeout=30, creationflags=NO_WINDOW,
        )
    else:
        target.symlink_to(source, target_is_directory=True)


def reconcile(repo: Path, action: str, home: Optional[Path] = None) -> dict:
    """Diagnose, install, or uninstall this checkout's individually owned links."""
    repo = repo.resolve()
    if not (repo / ".git").exists():
        raise ValueError(f"not a checkout: {repo}")
    manifest = load_manifest(repo)
    rows = []
    blocked = False

    def report(state: str, path: Path, detail: str) -> None:
        nonlocal blocked
        rows.append({"state": state, "path": str(path), "detail": detail})
        blocked |= state in {"collision", "unknown", "missing-source"}

    # Never traverse a replaced parent while touching a recorded child link.
    def safe_parent(target: Path) -> bool:
        return all(not is_link(p) for p in target.parents if p != repo and repo in p.parents)

    if action == "uninstall":
        for key, source in list(manifest["links"].items()):
            target = repo / key
            if not safe_parent(target):
                report("collision", target, "parent became a link; retained ownership record")
            elif not present(target):
                del manifest["links"][key]
                report("absent", target, "already removed")
            elif not is_link(target) or str(target.resolve()) != source:
                report("collision", target, "entry changed; retained entry and ownership record")
            else:
                _unlink(target)
                del manifest["links"][key]
                report("removed", target, source)
        if manifest["links"] or manifest_path(repo).exists():
            _save(repo, manifest)
        if not manifest["links"] and manifest_path(repo).exists():
            manifest_path(repo).unlink()
        return {"repo": str(repo), "state": "blocked" if blocked else "ok", "rows": rows}

    scopes = scopes_in(repo)
    for scope in scopes:
        for root in ROOTS:
            folder = scope / root / "skills"
            if folder.is_dir():
                for entry in folder.iterdir():
                    if is_link(entry) and not entry.exists() and entry.relative_to(repo).as_posix() not in manifest["links"]:
                        report("unknown", entry, "unowned broken link; preserve and inspect its source")
    skills = [skill for scope in scopes for skill in skills_in(scope)]
    user_skills = skills_in(home) if home else []
    ancestor_skills = [s for ancestor in repo.parents if ancestor != home for s in skills_in(ancestor)]
    for scope in scopes:
        visible = [s for s in skills if s.scope == scope or s.scope in scope.parents] + user_skills + ancestor_skills
        names: dict[str, set[Path]] = {}
        for skill in visible:
            names.setdefault(skill.name, set()).add(skill.source)
        collisions = {name for name, sources in names.items() if len(sources) > 1}
        for name in sorted(collisions):
            report("collision", scope, f"duplicate name {name}: select/rename the maintained source explicitly")
        scoped = [s for s in skills if s.scope == scope]
        for source in sorted({s.source for s in scoped}, key=str):
            routes = [s for s in scoped if s.source == source]
            skill = routes[0]
            if skill.name in collisions:
                continue
            if any(s.source == source and s.scope != scope for s in visible):
                report("collision", skill.path, "same source already visible in an ancestor/user scope; remove the redundant route explicitly")
                continue
            if not source.is_relative_to(repo.resolve()):
                report("collision", skill.path, "project source resolves outside this checkout; no primary/private links in worktrees")
                continue
            for root in ROOTS:
                existing = [s for s in routes if s.path.parent.parent.name == root]
                if existing:
                    report("linked" if is_link(existing[0].path) else "source", existing[0].path, str(source))
                    if len(existing) > 1:
                        report("collision", scope / root, f"same source listed through {len(existing)} routes: {skill.name}")
                    continue
                target = scope / root / "skills" / skill.path.name
                key = target.relative_to(repo).as_posix()
                if not safe_parent(target):
                    report("collision", target, "parent is a link; preserve it and select a safe source manually")
                elif present(target):
                    report("collision", target, "occupied entry; compare content and explicitly select/rename; nothing overwritten")
                elif action == "install":
                    _link(source, target)
                    manifest["links"][key] = str(source)
                    # The shared exclude lock may be held by a sibling checkout.
                    # Keep this successfully created link owned even if that
                    # separate metadata update must report unknown/retry.
                    _persist(repo, manifest)
                    _save(repo, manifest)
                    report("created", target, str(source))
                else:
                    report("missing-link", target, str(source))

    # Source removal/move is reconciled only for unchanged, manifest-owned links.
    for key, source in list(manifest["links"].items()):
        target = repo / key
        if not safe_parent(target) or (present(target) and (not is_link(target) or str(target.resolve()) != source)):
            report("collision", target, "owned entry changed; preserve it and resolve manually")
        elif not (Path(source) / "SKILL.md").is_file():
            if action == "install":
                if present(target):
                    _unlink(target)
                del manifest["links"][key]
                report("removed", target, "maintained source was removed")
            else:
                report("missing-source", target, source)
    if action == "install" and (manifest["links"] or manifest_path(repo).exists()):
        _save(repo, manifest)
    state = "blocked" if blocked else "needs-install" if any(r["state"] == "missing-link" for r in rows) else "ok"
    return {"repo": str(repo), "state": state, "rows": rows,
            "native_discovery": "unknown: run the opt-in synthetic client probe"}


def instruction_report(repo: Path, home: Path) -> dict:
    """Report byte counts and pointer presence without claiming model obedience."""
    paths = [home / ".claude/CLAUDE.md", home / ".codex/AGENTS.md", home / ".pi/agent/AGENTS.md"]
    scopes = [repo] + [p for p in scopes_in(repo) if p != repo]
    paths += [scope / name for scope in scopes for name in ("AGENTS.md", "CLAUDE.md")]
    chains = []
    for scope in scopes:
        pointer, source = scope / "AGENTS.md", scope / "CLAUDE.md"
        override = scope / "AGENTS.override.md"
        if override.is_file() and override.stat().st_size:
            paths.append(override)
            state = "override: Codex selects AGENTS.override.md before AGENTS.md"
        elif not pointer.exists() and not source.exists():
            state = "inherited: no local instruction files"
        elif not pointer.is_file() or not source.is_file():
            state = "missing-pointer-or-source"
        elif "CLAUDE.md" not in pointer.read_text(encoding="utf-8-sig"):
            state = "unknown: AGENTS.md is not a recognizable CLAUDE.md pointer"
        else:
            state = "available: pointer and source exist; following the pointer is unverified"
        chains.append({"scope": str(scope), "state": state})
    config = home / ".codex/config.toml"
    settings = tomllib.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    return {"state": "unknown: filesystem bytes do not prove instruction reading",
            "chains": chains,
            "global_links_share_source": (len({p.resolve() for p in paths[:3]}) == 1
                                          if all(p.is_file() for p in paths[:3]) else None),
            "codex_project_doc_max_bytes": settings.get("project_doc_max_bytes", "unset: query active runtime"),
            "codex_project_doc_fallback_filenames": settings.get("project_doc_fallback_filenames", "unset"),
            "files": [{"path": str(p), "resolved": str(p.resolve()),
                       "bytes": p.stat().st_size if p.is_file() else None} for p in paths]}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("diagnose", "install", "uninstall"))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo", type=Path)
    group.add_argument("--registered", action="store_true")
    parser.add_argument("--home", type=Path, default=Path.home())
    args = parser.parse_args(argv)
    repos = [args.repo] if args.repo else list(fleet_repos().values())
    results = []
    for repo in repos:
        try:
            result = reconcile(repo, args.action, args.home)
            if args.action == "diagnose":
                result["instructions"] = instruction_report(repo, args.home)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            result = {"repo": str(repo), "state": "unknown", "reason": str(exc)}
        results.append(result)
    print(json.dumps(results, indent=2))
    return int(any(r["state"] != "ok" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
