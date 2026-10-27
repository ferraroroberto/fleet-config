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
SRC = {"nav": "web/nav", "tool": "lib/tool.py", "modal": "web/modal"}
V1 = {"web/nav/nav.css": ".a{}\n", "lib/tool.py": "v1\n", "web/modal/modal.css": ".m{}\n.m input{}\n"}
V2 = {"web/nav/nav.css": ".a{}\n.c{}\n", "lib/tool.py": "v2\n", "web/modal/modal.css": ".m{}\n"}


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
    (scaffold / ".fleet.toml").write_text(
        "[components]\n" + "\n".join(f'{k} = {{ src = "{v}" }}' for k, v in SRC.items()) + "\n", encoding="utf-8")

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
finally:
    shutil.rmtree(root, ignore_errors=True)


_h.report_and_exit("test_vendored_wave")
