"""Contracts between fleet repos: the `.fleet.toml` keys that declare them, and
the scan that finds the ones a repo uses without declaring.

A card says what a repo is; these keys say what it exposes (`interfaces`), what
it is and is not responsible for (`owns` / `not_owns`) and which other fleet
repos it relies on (`depends_on`). `validate` is the one schema check, called by
`/system-map`'s `build_data.py`; `card_fields` is what lands on the map card;
`scan_repo` reads each repo's committed tree for references to another
fleet repo, so a dependency the card does not declare is reported by name.
Schema: architecture/README.md.

stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_run  # noqa: E402

INTERFACE_KINDS = ("http", "cli", "file", "lib")


class UnknownDependency(ValueError):
    """A `depends_on` entry naming a repo that is not in the fleet."""


def _str_list(repo_name: str, key: str, raw: Any) -> List[str]:
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ValueError(f"{repo_name}: .fleet.toml `{key}` must be a list of non-empty strings, got {raw!r}")
    return list(raw)


def _interfaces(repo_name: str, raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        raise ValueError(f"{repo_name}: .fleet.toml `interfaces` must be a list of tables, got {raw!r}")
    out, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"name", "kind", "doc"} or not all(
            isinstance(v, str) and v.strip() for v in entry.values()
        ):
            raise ValueError(
                f"{repo_name}: .fleet.toml interface must be {{name, kind, doc}} strings, got {entry!r}"
            )
        if entry["kind"] not in INTERFACE_KINDS:
            raise ValueError(
                f"{repo_name}: .fleet.toml interface {entry['name']!r} kind must be one of "
                f"{list(INTERFACE_KINDS)}, got {entry['kind']!r}"
            )
        if entry["name"] in seen:
            raise ValueError(f"{repo_name}: .fleet.toml interface {entry['name']!r} is declared twice")
        seen.add(entry["name"])
        out.append({"name": entry["name"], "kind": entry["kind"], "doc": entry["doc"]})
    return out


def _depends_on(repo_name: str, raw: Any, known_repos: Optional[Set[str]]) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        raise ValueError(f"{repo_name}: .fleet.toml `depends_on` must be a list, got {raw!r}")
    out = []
    for entry in raw:
        if isinstance(entry, str):
            dep = {"repo": entry}
        elif isinstance(entry, dict) and set(entry) in ({"repo"}, {"repo", "interface"}):
            dep = dict(entry)
        else:
            raise ValueError(
                f"{repo_name}: .fleet.toml `depends_on` entries are a repo name or "
                f"{{repo, interface}}, got {entry!r}"
            )
        if not all(isinstance(v, str) and v.strip() for v in dep.values()):
            raise ValueError(f"{repo_name}: .fleet.toml `depends_on` entry {entry!r} has an empty value")
        if dep["repo"] == repo_name:
            raise ValueError(f"{repo_name}: .fleet.toml `depends_on` names the repo itself")
        if known_repos is not None and dep["repo"] not in known_repos:
            raise UnknownDependency(
                f"{repo_name}: .fleet.toml `depends_on` names {dep['repo']!r}, which is not a fleet repo "
                "(hooks/projects.toml minus architecture_ignore)."
            )
        out.append({k: dep[k] for k in ("repo", "interface") if k in dep})
    return out


def validate(repo_name: str, meta: Mapping[str, Any], known_repos: Optional[Set[str]] = None) -> Dict[str, Any]:
    """The contract keys of a parsed `.fleet.toml`, normalized, or `ValueError`
    naming the repo and the bad value. All four keys are optional; absent ones
    are left out. With `known_repos`, a `depends_on` repo outside it raises
    `UnknownDependency`."""
    out: Dict[str, Any] = {}
    if "interfaces" in meta:
        out["interfaces"] = _interfaces(repo_name, meta["interfaces"])
    for key in ("owns", "not_owns"):
        if key in meta:
            out[key] = _str_list(repo_name, key, meta[key])
    if "depends_on" in meta:
        out["depends_on"] = _depends_on(repo_name, meta["depends_on"], known_repos)
    return out


def card_fields(contracts: Mapping[str, Any]) -> Dict[str, Any]:
    """The map-card keys for validated contracts (`deps`, `ifs`, `owns`, `not_owns`)."""
    names = {"depends_on": "deps", "interfaces": "ifs", "owns": "owns", "not_owns": "not_owns"}
    return {names[k]: v for k, v in contracts.items() if v}


class MissingInterfaceDoc(ValueError):
    """An interface whose `doc` path is not in the repo."""


def check_docs(repo_name: str, repo_dir: Path, ref: Optional[str], interfaces: Iterable[Mapping[str, str]]) -> None:
    """Raise `MissingInterfaceDoc` naming the repo when an interface `doc` is
    absent from the committed `ref`, or from the working tree when `ref` is None."""
    def present(doc: str) -> bool:
        if ref is None:
            return (repo_dir / doc).is_file()
        return git_run.run_git(["-C", str(repo_dir), "cat-file", "-e", f"{ref}:{doc}"]).returncode == 0

    missing = [i["doc"] for i in interfaces if not present(i["doc"])]
    if missing:
        raise MissingInterfaceDoc(f"{repo_name}: .fleet.toml interface doc(s) not in the repo: {missing}")


_PORT_RE = re.compile(r":(\d{2,5})\b")
# Paths whose mention of another repo is not a runtime dependency: prose, the
# card itself, and test fixtures that use a sibling's path as sample data.
_NOT_COUPLING = (":(exclude)*.md", ":(exclude).fleet.toml", ":(exclude)tests/*",
                 ":(exclude)*/tests/*", ":(exclude)*test_*")
_NAME_TAIL = r"(?![A-Za-z0-9_-])"


def port_owners(cards: Iterable[Mapping[str, Any]]) -> Dict[str, Set[str]]:
    """`{port: {repo, ...}}` from each card's `port` and its `chips` (a chip names
    a sub-service the repo runs, e.g. `whisper :8090`). A port two repos share
    maps to both, and a reference to it is declared when either is."""
    owners: Dict[str, Set[str]] = {}
    for card in cards:
        repo = card.get("repo", card["nm"])
        ports = [str(card["port"]).lstrip(":")] if card.get("port") else []
        ports += [m.group(1) for chip in card.get("chips", []) for m in _PORT_RE.finditer(chip)]
        for port in ports:
            owners.setdefault(port, set()).add(repo)
    return owners


def _patterns(repos: Iterable[str], ports: Mapping[str, Set[str]]) -> tuple:
    """`(git grep prefilter, [(regex, target repos)])`, longest repo name first
    so `local-llm-hub-lite` is never read as `local-llm-hub`."""
    names = sorted(repos, key=len, reverse=True)
    res = [(re.compile(r"(?:automation|\.\.)[/\\]+" + re.escape(n) + _NAME_TAIL), {n}) for n in names]
    res += [(re.compile(r"(?:127\.0\.0\.1|localhost):" + p + r"\b"), set(o)) for p, o in sorted(ports.items())]
    # git grep -E is POSIX ERE (no lookahead), so it only narrows the lines;
    # the Python regexes above decide each match.
    alt_names = "|".join(n.replace(".", r"\.") for n in names) or "no-fleet-repo"
    alt_ports = "|".join(sorted(ports)) or "0"
    prefilter = rf"(automation|\.\.)[/\\]+({alt_names})|(127\.0\.0\.1|localhost):({alt_ports})"
    return prefilter, res


def scan_repo(
    repo_name: str, repo_dir: Path, ref: str, repos: Iterable[str], ports: Mapping[str, Set[str]],
) -> Optional[Dict[str, str]]:
    """`{target-label: "path:line"}` for every other fleet repo `repo_name`'s
    committed `ref` refers to by sibling path or loopback port, outside
    `_NOT_COUPLING`. `None` when git grep could not run, so an unscanned repo
    is never reported as clean."""
    prefilter, res = _patterns(repos, ports)
    proc = git_run.run_git([
        "-C", str(repo_dir), "grep", "-I", "-n", "-E", prefilter, ref, "--", ".", *_NOT_COUPLING,
    ])
    if proc.returncode not in (0, 1):  # 1 = no match
        return None
    found: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        rest = line[len(ref) + 1:] if line.startswith(ref + ":") else line
        path, _, rest = rest.partition(":")
        lineno, _, text = rest.partition(":")
        for regex, targets in res:
            if repo_name in targets or not regex.search(text):
                continue
            found.setdefault("|".join(sorted(targets)), f"{path}:{lineno}")
    return found


def undeclared(found: Mapping[str, str], deps: Iterable[Mapping[str, str]]) -> Dict[str, str]:
    """The references in `found` no declared dependency covers."""
    declared = {d["repo"] for d in deps}
    return {label: where for label, where in found.items() if not declared & set(label.split("|"))}
