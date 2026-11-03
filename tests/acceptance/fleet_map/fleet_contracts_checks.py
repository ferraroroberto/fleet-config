"""The `.fleet.toml` contract keys (`interfaces`, `owns`, `not_owns`,
`depends_on`): validation, the refusal of a bad `depends_on` by repo name, the
dotted map edges, the generated contracts page, and the coupling scan.

Hard: every input is a fixture or this repo's own committed files, so a
sibling repo's commit can never turn it red. The fleet-wide coupling report
is `build_data.py --coupling`, run by `/system-map`, not here; only its exit-code logic is checked here.

Not in `architecture_guards.py`: that file is near the structure limit.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
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
        # Sorts before src.py, so unexcluded it would claim app-b's one report slot.
        (repo / "jobs.sample.json").write_text(
            '{"script": "E:/automation/app-b/run.bat", "alt": "../app-c/run.bat"}\n', encoding="utf-8")
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
        ignore = [{"path": "jobs.sample.json", "repo": "app-b", "why": "sample job data"}]
        raw = fc.scan_repo("app-a", repo, "main", repos, ports)
        check("fleet_contracts: without ignore_refs a sample file claims the target's report slot",
              raw is not None and raw.get("app-b") == "jobs.sample.json:1" and "app-c" in raw, str(raw))
        found = fc.scan_repo("app-a", repo, "main", repos, ports, ignore)
        check("fleet_contracts: scan finds sibling-path and loopback-port references, not prose, tests or ignore_refs",
              found is not None and set(found) == {"app-b", "app-b-lite", "hub"}
              and found["hub"] == "src.py:1" and found["app-b"] == "src.py:2", str(found))
        left = fc.undeclared(found or {}, [{"repo": "hub"}, {"repo": "app-b-lite"}])
        check("fleet_contracts: a declared dependency clears its references",
              set(left) == {"app-b"}, str(left))
        check("fleet_contracts: an unscannable repo is None, never clean",
              fc.scan_repo("app-a", repo, "no-such-ref", repos, ports) is None)


def _ignore_refs_checks(check: _Checker, bd, fc, base: dict, known: set, plain: dict) -> None:
    """`[contracts] ignore_refs`: validation, refusals by repo name, off the map card."""
    ig = {"path": "config/jobs.sample.json", "repo": "app-b", "why": "sample job data"}
    ok = fc.validate("ghost-repo", {"contracts": {"ignore_refs": [ig]}}, known)
    _, with_ig = bd.card_from_toml("ghost-repo", {**base, "contracts": {"ignore_refs": [ig]}}, known)
    check("fleet_contracts: ignore_refs validates and stays off the map card",
          ok == {"ignore_refs": [ig]} and with_ig == plain, str(with_ig))
    check("fleet_contracts: a card without the key validates as before",
          fc.validate("ghost-repo", {**base}, known) == {})
    for label, bad in (
        ("missing why", {"contracts": {"ignore_refs": [{"path": "a.json", "repo": "app-b"}]}}),
        ("empty why", {"contracts": {"ignore_refs": [{**ig, "why": " "}]}}),
        ("absolute path", {"contracts": {"ignore_refs": [{**ig, "path": "/etc/x"}]}}),
        ("parent path", {"contracts": {"ignore_refs": [{**ig, "path": "../x"}]}}),
        ("pathspec magic", {"contracts": {"ignore_refs": [{**ig, "path": ":(top)x"}]}}),
        ("duplicate path", {"contracts": {"ignore_refs": [ig, ig]}}),
        ("self", {"contracts": {"ignore_refs": [{**ig, "repo": "ghost-repo"}]}}),
        ("unknown key", {"contracts": {"allow_refs": []}}),
        ("shape", {"contracts": {"ignore_refs": "a.json"}}),
    ):
        _, msg = _refusal(lambda bad=bad: bd.card_from_toml("ghost-repo", {**base, **bad}, known))
        check(f"fleet_contracts: a bad ignore_refs ({label}) is refused naming the repo",
              "ghost-repo" in msg, msg or "accepted")
    kind, msg = _refusal(lambda: bd.card_from_toml(
        "ghost-repo", {**base, "contracts": {"ignore_refs": [{**ig, "repo": "nope"}]}}, known))
    check("fleet_contracts: an ignore_refs repo outside the fleet is refused by repo and target name",
          kind == "UnknownDependency" and "ghost-repo" in msg and "'nope'" in msg, msg or "accepted")
    toml = 'layer = "enabling"\nicon = "x"\ndescription = "A hub."\ndepends_on = ["nope"]\n'
    real_read = bd.read_fleet_toml
    try:
        bd.read_fleet_toml = lambda _repo_dir: (
            'layer = "enabling"\nicon = "x"\ndescription = "A hub."\n[contracts]\n'
            'ignore_refs = [{ path = "a.json", repo = "app-b", why = "sample" }]\n')
        check("fleet_contracts: the coupling scan reads ignore_refs from the committed card",
              bd._ignore_refs("ghost-repo", REPO, known) == [{"path": "a.json", "repo": "app-b", "why": "sample"}])
        bd.read_fleet_toml = lambda _repo_dir: toml  # a malformed declaration ignores nothing
        check("fleet_contracts: a malformed card ignores nothing in the coupling scan",
              bd._ignore_refs("ghost-repo", REPO, known) == [])
    finally:
        bd.read_fleet_toml = real_read


def _coupling_exit_checks(check: _Checker, bd) -> None:
    """`--coupling` exits 1 on an undeclared reference, an unscanned repo or a
    refused card, and 0 only when every repo was scanned clean."""
    saved = (bd.fleet_repos, bd.build, bd.coupling)
    try:
        bd.fleet_repos = lambda _toml=None: {}
        for label, report, refused, want in (
            ("a clean fleet", {"a": {}, "b": {}}, [], 0),
            ("an undeclared reference", {"a": {"b": "x.py:1"}, "b": {}}, [], 1),
            ("a repo that could not be scanned", {"a": {}, "b": None}, [], 1),
            ("a refused card", {"a": {}, "b": {}}, ["b: description too long."], 1),
        ):
            bd.build = lambda _res, _repos, out, refused=refused: out.extend(refused) or {}
            bd.coupling = lambda _data, _repos, report=report: report
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = bd._print_coupling()
            check(f"fleet_contracts: --coupling exits {want} on {label}", code == want, out.getvalue())
        text = out.getvalue()
        check("fleet_contracts: the --coupling summary names what it counts",
              "undeclared reference(s) in 0 of 2 repo(s); 0 not scanned; 1 card(s) refused" in text, text)
    finally:
        bd.fleet_repos, bd.build, bd.coupling = saved


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

    _ignore_refs_checks(check, bd, fc, base, known, plain)

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
    _coupling_exit_checks(check, bd)
    return check.failures, check.total
