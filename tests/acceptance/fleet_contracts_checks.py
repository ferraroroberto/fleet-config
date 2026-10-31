"""The `.fleet.toml` contract keys (`interfaces`, `owns`, `not_owns`,
`depends_on`): validation, the refusal of a bad `depends_on` by repo name, the
dotted map edges, the generated contracts page, and the coupling scan.

Hard: every input is a fixture or this repo's own committed files, so a
sibling repo's commit can never turn it red. The fleet-wide coupling report
is `build_data.py --coupling`, run by `/system-map`, not here.

Lives beside `architecture_guards.py` rather than in it: that file is near
the structure limit.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Tuple

from acceptance.shared import REPO, _Checker

_SYSTEM_MAP = REPO / ".claude" / "skills" / "system-map"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _refusal(fn) -> Tuple[str, str]:
    """`(exception class name, message)` of what `fn` raises, or `("", "")`."""
    try:
        fn()
    except ValueError as exc:
        return type(exc).__name__, str(exc)
    return "", ""


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()


def _coupling_fixture(check: _Checker, fc) -> None:
    """A throwaway repo whose committed tree references two siblings, once
    each in code and again in places that are not coupling."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "app-a"
        (repo / "tests").mkdir(parents=True)
        (repo / "src.py").write_text(
            'HUB = "http://127.0.0.1:8000/v1"\nDB = "E:/automation/app-b/data.db"\n'
            'NOT = "E:/automation/app-b-lite/x"\n', encoding="utf-8")
        (repo / "README.md").write_text("see E:/automation/app-c/README.md\n", encoding="utf-8")
        (repo / "tests" / "test_x.py").write_text('P = "../app-c/x"\n', encoding="utf-8")
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        # Plumbing, not `git commit`: a fixture commit is not the user's, so it
        # must not meet the machine's commit hooks (an author allowlist).
        commit = _git(repo, "commit-tree", _git(repo, "write-tree"), "-m", "fixture")
        _git(repo, "update-ref", "refs/heads/main", commit)

        repos = ["app-a", "app-b", "app-b-lite", "app-c", "hub"]
        ports = fc.port_owners([{"nm": "hub", "port": ":8000"}, {"nm": "app-a", "chips": ["svc :9999"]}])
        check("fleet_contracts: port_owners reads `port` and chip ports",
              ports == {"8000": {"hub"}, "9999": {"app-a"}}, str(ports))
        found = fc.scan_repo("app-a", repo, "main", repos, ports)
        check("fleet_contracts: scan finds sibling-path and loopback-port references, not prose or tests",
              found is not None and set(found) == {"app-b", "app-b-lite", "hub"}
              and found["hub"] == "src.py:1", str(found))
        left = fc.undeclared(found or {}, [{"repo": "hub"}, {"repo": "app-b-lite"}])
        check("fleet_contracts: a declared dependency clears its references",
              set(left) == {"app-b"}, str(left))
        check("fleet_contracts: an unscannable repo is None, never clean",
              fc.scan_repo("app-a", repo, "no-such-ref", repos, ports) is None)


def _fleet_contracts_check() -> Tuple[int, int]:
    """Contract keys validate, refuse a bad dependency by name, and render."""
    check = _Checker()
    sys.path.insert(0, str(_SYSTEM_MAP))
    bd = _load("system_map_build_data_contracts", _SYSTEM_MAP / "build_data.py")
    rm = _load("system_map_render_mermaid_contracts", _SYSTEM_MAP / "render_mermaid.py")
    rc = _load("system_map_render_contracts", _SYSTEM_MAP / "render_contracts.py")
    fc = bd.fleet_contracts

    base = {"layer": "enabling", "icon": "🧠", "description": "A hub."}
    keys = {
        "interfaces": [{"name": "messages", "kind": "http", "doc": "docs/api.md"}],
        "owns": ["routing"], "not_owns": ["weights"],
        "depends_on": ["app-b", {"repo": "app-c", "interface": "jobs"}],
    }
    known = {"ghost-repo", "app-b", "app-c"}
    _, plain = bd.card_from_toml("ghost-repo", base, known)
    _, card = bd.card_from_toml("ghost-repo", {**base, **keys}, known)
    check("fleet_contracts: the keys land on the card as deps/ifs/owns/not_owns, nothing else changes",
          {k: v for k, v in card.items() if k not in ("deps", "ifs", "owns", "not_owns")} == plain
          and card["deps"] == [{"repo": "app-b"}, {"repo": "app-c", "interface": "jobs"}]
          and card["ifs"] == keys["interfaces"] and card["owns"] == ["routing"], str(card))

    kind, msg = _refusal(lambda: bd.card_from_toml("ghost-repo", {**base, "depends_on": ["nope"]}, known))
    check("fleet_contracts: a depends_on outside the fleet is refused by repo and dependency name",
          kind == "UnknownDependency" and "ghost-repo" in msg and "'nope'" in msg, msg or "accepted")
    for label, bad in (
        ("kind", {"interfaces": [{"name": "x", "kind": "grpc", "doc": "a.md"}]}),
        ("missing doc", {"interfaces": [{"name": "x", "kind": "http"}]}),
        ("duplicate name", {"interfaces": [{"name": "x", "kind": "http", "doc": "a"}] * 2}),
        ("owns", {"owns": "routing"}),
        ("self", {"depends_on": ["ghost-repo"]}),
        ("entry shape", {"depends_on": [{"name": "app-b"}]}),
    ):
        _, msg = _refusal(lambda bad=bad: bd.card_from_toml("ghost-repo", {**base, **bad}, known))
        check(f"fleet_contracts: a bad {label} is refused naming the repo", "ghost-repo" in msg, msg or "accepted")

    toml = 'layer = "enabling"\nicon = "🧠"\ndescription = "A hub."\ndepends_on = ["nope"]\n'
    residual = {"enabling": [{"ic": "🧠", "nm": "ghost-repo", "ds": "fallback"}]}
    real_read = bd.read_fleet_toml
    bd.read_fleet_toml = lambda _repo_dir: toml
    try:
        refused: list = []
        built = bd.build(residual, {"ghost-repo": REPO}, refused)
        check("fleet_contracts: build keeps the fallback card for an unknown dependency and names it",
              built["enabling"] == residual["enabling"] and len(refused) == 1 and "'nope'" in refused[0],
              str(refused))
    finally:
        bd.read_fleet_toml = real_read

    kind, msg = _refusal(lambda: fc.check_docs("ghost-repo", REPO, None, [{"doc": "no/such.md"}]))
    check("fleet_contracts: an interface doc missing from the repo is refused by name",
          kind == "MissingInterfaceDoc" and "ghost-repo" in msg and "no/such.md" in msg, msg or "accepted")

    data = {"enabling": [{"ic": "🧠", "nm": "ghost-repo", "ds": "A hub.", **{k: card[k] for k in ("deps", "ifs", "owns", "not_owns")}},
                         {"ic": "🚀", "nm": "app-b", "ds": "B."}, {"ic": "🚀", "nm": "app-c", "ds": "C."}]}
    mmd = rm.render(data)
    check("fleet_contracts: the map draws each depends_on as a dotted edge, labelled with its interface",
          "  ghost_repo -.-> app_b\n" in mmd and "  ghost_repo -.->|jobs| app_c\n" in mmd, mmd)
    page = rc.render(data)
    check("fleet_contracts: the contracts page lists every interface with its doc link",
          "| ghost-repo | messages | http | [docs/api.md](https://github.com/" in page
          and "/ghost-repo/blob/main/docs/api.md) |" in page
          and "| ghost-repo | app-b, app-c (jobs) |" in page and "| app-b |" not in page,
          page)

    committed = (REPO / "architecture" / "CONTRACTS.md").read_text(encoding="utf-8")
    check("fleet_contracts: CONTRACTS.md matches render_contracts.py output", rc.regenerate() == committed,
          "regenerate: E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/render_mermaid.py")

    _coupling_fixture(check, fc)
    return check.failures, check.total
