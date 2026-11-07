"""Unit tests for skills/_lib/vendored_wave/plan.py: one propagation PR per repo per wave.

Builds a real throwaway scaffold git repo (three components, two commits each) and adopter
repos with different pins, then proves the plan groups every component a repo is behind on
into one entry with one branch and one PR body, that a single-component run keeps the
branch, commit subject and PR shape it always had, and that held components and carriers
are reported instead of targeted. No real fleet repo is touched.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/repo/test_vendored_wave.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
import vendored_drift as vd  # noqa: E402
from vendored_wave import ignore_block as ib  # noqa: E402
from vendored_wave import plan as vw  # noqa: E402

sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from git_fixtures import run_git  # noqa: E402

_h = CheckHarness()
check = _h.check

CLOSING = re.compile(r"\b(close[sd]?|fix(es|ed)?|resolve[sd]?)\b", re.I)
SRC = {"nav": "web/nav", "tool": "lib/tool.py", "modal": "web/modal",
       "ratchet": "lib/ratchet.py", "readers": "lib/readers.py", "outer": "lib/outer.py"}
# `ratchet` loads `readers` at runtime and `outer` loads `ratchet`: the shape of conventions_ratchet.
REQUIRES = {"ratchet": ["readers"], "outer": ["ratchet"]}
V1 = {"web/nav/nav.css": ".a{}\n", "lib/tool.py": "v1\n", "web/modal/modal.css": ".m{}\n.m input{}\n",
      "lib/ratchet.py": "r1\n", "lib/readers.py": "d1\n", "lib/outer.py": "o1\n"}
V2 = {"web/nav/nav.css": ".a{}\n.c{}\n", "lib/tool.py": "v2\n", "web/modal/modal.css": ".m{}\n",
      "lib/ratchet.py": "r2\n", "lib/readers.py": "d2\n", "lib/outer.py": "o2\n"}


def _catalog(requires: dict) -> str:
    rows = []
    for key, src in SRC.items():
        extra = f', requires = {requires[key]!r}'.replace("'", '"') if key in requires else ""
        rows.append(f'{key} = {{ src = "{src}"{extra} }}')
    return "[components]\n" + "\n".join(rows) + "\n"


def _git(cwd: Path, *args: str) -> str:
    return run_git(cwd, *args, check=check)


def _write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="")


def _adopter(root: Path, name: str, pins: dict) -> Path:
    """A repo vendoring each `component -> sha` in `pins`, its files byte-identical to that commit."""
    d = root / name
    entries = []
    for comp, (sha, files) in pins.items():
        _write(d, {rel: text for rel, text in files.items() if rel.startswith(SRC[comp])})
        entries.append(f'{comp} = {{ src = "{SRC[comp]}", sha = "{sha}", dest = "{SRC[comp]}" }}')
    (d / ".fleet.toml").write_text('layer = "working-web"\nicon = "x"\ndescription = "d"\n\n[vendored]\n'
                                   + "\n".join(entries) + "\n", encoding="utf-8")
    ib.write_repo(d, [SRC[comp] for comp in pins])
    return d


root = Path(tempfile.mkdtemp(prefix="vendored_wave_"))
try:
    scaffold = root / "project-scaffolding"
    scaffold.mkdir()
    _git(scaffold, "init", "-q")
    _git(scaffold, "checkout", "-q", "-b", "main")
    _git(scaffold, "config", "user.email", "35553560+ferraroroberto@users.noreply.github.com")
    _git(scaffold, "config", "user.name", "Test")
    _write(scaffold, V1)
    _git(scaffold, "add", "-A")
    _git(scaffold, "commit", "-q", "-m", "v1")
    v1 = _git(scaffold, "rev-parse", "HEAD")
    _write(scaffold, V2)
    _git(scaffold, "add", "-A")
    _git(scaffold, "commit", "-q", "-m", "v2")
    v2 = _git(scaffold, "rev-parse", "HEAD")
    (scaffold / ".fleet.toml").write_text(_catalog(REQUIRES), encoding="utf-8")

    repos = {
        "project-scaffolding": scaffold,
        # behind on nav and tool -> must collapse to ONE entry
        "two-behind": _adopter(root, "two-behind", {"nav": (v1, V1), "tool": (v1, V1)}),
        # behind on nav only; tool already current
        "one-behind": _adopter(root, "one-behind", {"nav": (v1, V1), "tool": (v2, V2)}),
        # current on everything
        "current": _adopter(root, "current", {"nav": (v2, V2), "tool": (v2, V2)}),
        # behind on a component whose update drops a selector
        "breaking": _adopter(root, "breaking", {"modal": (v1, V1), "tool": (v1, V1)}),
    }
    carrier_same = root / "carrier-same"
    _write(carrier_same, {"lib/tool.py": V2["lib/tool.py"]})
    (carrier_same / ".fleet.toml").write_text('layer = "working-web"\nicon = "x"\ndescription = "d"\n', encoding="utf-8")
    carrier_fork = root / "carrier-fork"
    _write(carrier_fork, {"lib/tool.py": "forked\n"})
    (carrier_fork / ".fleet.toml").write_text('layer = "working-web"\nicon = "x"\ndescription = "d"\n', encoding="utf-8")
    repos["carrier-same"], repos["carrier-fork"] = carrier_same, carrier_fork

    # ---- multi-component scan filter ----
    check(vd.parse_component_filter("nav, tool") == frozenset({"nav", "tool"}), "parse_component_filter: comma list -> set")
    check(vd.parse_component_filter("") is None and vd.parse_component_filter(None) is None,
          "parse_component_filter: empty -> None (every component)")
    only_two = vd.scan_fleet(scaffold, frozenset({"nav", "tool"}), repos=repos)
    check({a["component"] for a in only_two["adopters"]} == {"nav", "tool"},
          "scan_fleet: a set filter keeps exactly the named components")
    check({a["repo"] for a in vd.scan_fleet(scaffold, "nav", repos=repos)["adopters"]} == {"two-behind", "one-behind", "current"},
          "scan_fleet: a single-name filter behaves as before (every declarer of that component, current or not)")

    # ---- one entry per repo, every component it is behind on ----
    report = vd.scan_fleet(scaffold, repos=repos)
    plan = vw.plan_wave(report, part_of="https://example.test/scaffold/pull/1")
    by_repo = {r["repo"]: r for r in plan["repos"]}
    check(set(by_repo) == {"two-behind", "one-behind", "breaking", "carrier-same"},
          f"plan: only repos with something to land get an entry ({sorted(by_repo)})")
    check("current" not in by_repo, "plan: a repo current on every component is not a target")

    tb = by_repo["two-behind"]
    check([c["component"] for c in tb["components"]] == ["nav", "tool"],
          "plan: a repo behind on two components carries both in ONE entry")
    check(tb["branch"] == f"chore/revendor-{v2[:7]}" and tb["worktree_key"] == f"revendor-{v2[:7]}",
          "plan: a multi-component entry has one branch and worktree key, named for the scaffold sha only")
    check(tb["commit_subject"] == f"chore: re-vendor 2 components from project-scaffolding@{v2[:7]}",
          "plan: multi-component commit subject counts the components")
    check(all(c["old_sha"] == v1 and c["new_sha"] == v2 for c in tb["components"]),
          "plan: each component records its own old and new sha")
    for comp in ("nav", "tool"):
        check(f"| `{comp}` | re-vendor | `{v1[:7]}` | `{v2[:7]}` |" in tb["pr_body"],
              f"plan: the PR body lists {comp} with its old and new sha")
    check("Part of https://example.test/scaffold/pull/1" in tb["pr_body"], "plan: the PR body links 'Part of <url>'")
    check(not CLOSING.search(tb["pr_body"] + tb["commit_body"] + tb["commit_subject"]),
          "plan: no closing keyword anywhere in the generated text")
    check(not re.search(r"[^\n]\n[^\n#|\-\s]", tb["pr_body"]), "plan: PR body paragraphs are not hard-wrapped")

    # ---- single-component run keeps today's shape ----
    ob = by_repo["one-behind"]
    check([c["component"] for c in ob["components"]] == ["nav"], "plan: only the components a repo is behind on are listed")
    check(ob["branch"] == f"chore/revendor-nav-{v2[:7]}" and ob["worktree_key"] == f"nav-{v2[:7]}"
          and ob["commit_subject"] == f"chore: re-vendor nav from project-scaffolding@{v2[:7]}",
          "plan: a one-component entry keeps the per-component branch, worktree key and commit subject")
    check("| Component |" not in ob["pr_body"] and f"`{v1[:7]}` -> `{v2[:7]}`" in ob["pr_body"],
          "plan: a one-component PR body is the one-line summary, with the old and new sha")
    single = vw.plan_wave(vd.scan_fleet(scaffold, "nav", repos=repos))
    check({r["repo"]: r["branch"] for r in single["repos"]}
          == {"two-behind": f"chore/revendor-nav-{v2[:7]}", "one-behind": f"chore/revendor-nav-{v2[:7]}"},
          "plan: `--component nav` yields the same per-repo branch as the one-component skill always did")

    # ---- held back, never targeted ----
    check(plan["held_contract"] == [{"repo": "breaking", "component": "modal", "pinned_sha": v1,
                                     "removed_selectors": [".m input"]}],
          f"plan: a component whose update drops a selector is held ({plan['held_contract']})")
    check([c["component"] for c in by_repo["breaking"]["components"]] == ["tool"],
          "plan: the held component does not ride along, the repo's other component still lands")
    allowed = vw.plan_wave(report, frozenset({"modal"}))
    check([c["component"] for c in {r["repo"]: r for r in allowed["repos"]}["breaking"]["components"]] == ["modal", "tool"]
          and allowed["held_contract"] == [],
          "plan: naming the component in allow_breaking releases it into the repo's PR")
    check(plan["held_carriers"] == [{"repo": "carrier-fork", "component": "tool", "path": "lib/tool.py",
                                     "diff_files": ["tool.py"]}],
          f"plan: a carrier that differs from the tip is reported, not targeted ({plan['held_carriers']})")
    adopt = by_repo["carrier-same"]["components"]
    check(len(adopt) == 1 and adopt[0]["action"] == "adopt" and adopt[0]["old_sha"] is None,
          "plan: a carrier identical to the tip is adopted with no old sha")
    check("not previously pinned" in by_repo["carrier-same"]["pr_body"], "plan: an adopt PR body says there was no prior pin")

    # ---- counts and the dry-run text ----
    check(plan["prs"] == 4 and plan["component_prs_before"] == 5,
          f"plan: counts PRs per repo against one PR per component ({plan['prs']} vs {plan['component_prs_before']})")
    text = vw.render_text(plan)
    check("two-behind   2 components -> 1 PR" in text and "nav   pinned" in text and "tool   pinned" in text,
          "render_text: the dry run lists, per repo, each component it is behind on")
    check("HELD (selectors removed): breaking modal" in text and "HUMAN CALL (differs from HEAD): carrier-fork tool" in text,
          "render_text: held components and carriers are named")
    check("catalog known" in text, "render_text: the coverage line states the catalog state")

    # ---- requires: a component brings the siblings it loads ----
    parsed, bad = vd.parse_scaffold_requires(_catalog(REQUIRES))
    check(parsed == REQUIRES and bad == [], "parse_scaffold_requires: reads each component's requires list")
    _, malformed = vd.parse_scaffold_requires('[components]\nratchet = { src = "a", requires = "readers" }\n')
    check(len(malformed) == 1 and "ratchet" in malformed[0]["error"],
          "parse_scaffold_requires: a requires that is not a list is a problem, not 'requires nothing'")
    closure, edges, problems = vd.expand_requires({"outer"}, REQUIRES, SRC)
    check(closure == {"outer", "ratchet", "readers"} and ("ratchet", "readers") in edges and problems == [],
          f"expand_requires: requires are followed transitively ({sorted(closure)})")
    check(vd.expand_requires({"tool"}, REQUIRES, SRC)[0] == {"tool"}, "expand_requires: a component with no requires expands to itself")
    cyc = vd.expand_requires({"a"}, {"a": ["b"], "b": ["a"]}, {"a": "x", "b": "y"})
    check(cyc[0] == {"a", "b"} and cyc[2] == [], "expand_requires: a cycle terminates and is not a problem")
    _, edges2, problems2 = vd.expand_requires({"outer"}, {"outer": ["ratchet"], "ratchet": ["ghost"]}, SRC)
    check(len(problems2) == 1 and problems2[0]["requires"] == "ghost" and "'ghost'" in problems2[0]["error"],
          "expand_requires: a dependency the catalog lacks is a problem that names it")
    check(set(vd.blocked_components(edges2, problems2)) == {"ratchet", "outer"},
          "blocked_components: the broken component and everything that requires it are blocked")

    req_repos = dict(repos)
    req_repos["needs-sibling"] = _adopter(root, "needs-sibling", {"ratchet": (v1, V1)})
    req_repos["has-both"] = _adopter(root, "has-both", {"ratchet": (v1, V1), "readers": (v1, V1)})
    req_repos["both-current"] = _adopter(root, "both-current", {"ratchet": (v2, V2), "readers": (v2, V2)})
    req_repos["current-no-sibling"] = _adopter(root, "current-no-sibling", {"ratchet": (v2, V2)})
    req_repos["outer-only"] = _adopter(root, "outer-only", {"outer": (v1, V1)})
    for name, files in (("ratchet-same", {"lib/ratchet.py": V2["lib/ratchet.py"]}),
                        ("ratchet-fork", {"lib/ratchet.py": "forked\n"})):
        d = root / name
        _write(d, files)
        (d / ".fleet.toml").write_text('layer = "working-web"\nicon = "x"\ndescription = "d"\n', encoding="utf-8")
        req_repos[name] = d

    rplan = vw.plan_wave(vd.scan_fleet(scaffold, frozenset({"ratchet"}), repos=req_repos))
    rby = {r["repo"]: {c["component"]: c for c in r["components"]} for r in rplan["repos"]}
    check(rplan["requires"] == [{"component": "ratchet", "requires": "readers"}] and rplan["held_requires"] == [],
          f"requires: naming ratchet lists its dependency ({rplan['requires']})")
    check(set(rby["needs-sibling"]) == {"ratchet", "readers"} and rby["needs-sibling"]["readers"]["action"] == "add"
          and rby["needs-sibling"]["readers"]["old_sha"] is None and rby["needs-sibling"]["readers"]["required_by"] == ["ratchet"],
          f"requires: a repo with ratchet and no readers gets readers added ({sorted(rby['needs-sibling'])})")
    check(set(rby["has-both"]) == {"ratchet", "readers"} and rby["has-both"]["readers"]["action"] == "re-vendor",
          "requires: a repo that declares both re-vendors both, adding nothing")
    check("both-current" not in rby, "requires: a repo current on both is not a target")
    check(set(rby["current-no-sibling"]) == {"readers"} and rby["current-no-sibling"]["readers"]["action"] == "add",
          "requires: a repo current on ratchet but missing readers still gets readers")
    check(set(rby["ratchet-same"]) == {"ratchet", "readers"} and rby["ratchet-same"]["ratchet"]["action"] == "adopt"
          and rby["ratchet-same"]["readers"]["action"] == "add",
          "requires: a ratchet carrier identical to the tip is adopted and gets readers")
    check("ratchet-fork" not in rby and any(c["repo"] == "ratchet-fork" for c in rplan["held_carriers"]),
          "requires: a ratchet carrier that differs pulls nothing in, it stays a human call")
    check("outer-only" not in rby, "requires: a repo declaring only a component outside the wave is untouched")
    check("`readers` is added because `ratchet` loads it at runtime." in
          {r["repo"]: r for r in rplan["repos"]}["current-no-sibling"]["pr_body"],
          "requires: the PR body says why the sibling is there")
    rtext = vw.render_text(rplan)
    check("REQUIRES=ratchet <- readers" in rtext and "readers   add (required by ratchet)" in rtext,
          "render_text: the dry run prints REQUIRES= lines and the add per repo")

    chain = vw.plan_wave(vd.scan_fleet(scaffold, frozenset({"outer"}), repos=req_repos))
    cby = {r["repo"]: {c["component"]: c for c in r["components"]} for r in chain["repos"]}
    check(set(cby["outer-only"]) == {"outer", "ratchet", "readers"}
          and cby["outer-only"]["readers"]["required_by"] == ["ratchet"],
          f"requires: a chain lands whole ({sorted(cby['outer-only'])})")
    check({e["requires"] for e in chain["requires"]} == {"ratchet", "readers"}, "requires: the plan lists every edge of the chain")

    full = vw.plan_wave(vd.scan_fleet(scaffold, repos=req_repos))
    fby = {r["repo"]: {c["component"]: c for c in r["components"]} for r in full["repos"]}
    check(fby["needs-sibling"]["readers"]["action"] == "add",
          "requires: a wave over every component adds a missing sibling too")

    # a dependency the scaffold cannot satisfy holds its dependents out and names the dependency
    (scaffold / ".fleet.toml").write_text(_catalog({**REQUIRES, "ratchet": ["ghost"]}), encoding="utf-8")
    broken = vw.plan_wave(vd.scan_fleet(scaffold, frozenset({"outer"}), repos=req_repos))
    check({h["component"] for h in broken["held_requires"]} == {"ratchet", "outer"}
          and all("'ghost'" in h["error"] for h in broken["held_requires"]),
          f"requires: a missing dependency blocks its dependents and names it ({broken['held_requires']})")
    check(not any(c["component"] in ("ratchet", "outer") for r in broken["repos"] for c in r["components"]),
          "requires: a blocked component is never half-planned")
    check(any("'ghost'" in e["error"] and e.get("kind") == "requires" for e in broken["errors"]),
          "requires: the scan errors carry the missing dependency")
    check("BLOCKED (dependency): outer" in vw.render_text(broken), "render_text: the blocked components are named")
    (scaffold / ".fleet.toml").write_text(_catalog({**REQUIRES, "readers": ["tool"]}), encoding="utf-8")
    _git(scaffold, "mv", "lib/tool.py", "lib/tool_moved.py")
    _git(scaffold, "commit", "-q", "-m", "move tool")
    gone = vw.plan_wave(vd.scan_fleet(scaffold, frozenset({"ratchet"}), repos=req_repos))
    check({h["component"] for h in gone["held_requires"]} == {"tool", "readers", "ratchet"}
          and any("no files at lib/tool.py" in e["error"] for e in gone["errors"]),
          f"requires: a dependency with no files at the tip blocks the chain ({gone['held_requires']})")
finally:
    shutil.rmtree(root, ignore_errors=True)


_h.report_and_exit("test_vendored_wave")
