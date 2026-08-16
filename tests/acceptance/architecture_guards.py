"""Architecture / fleet-map freshness guards (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (b) --
`/system-map` and `/config-map`'s coverage, `.fleet.toml` aggregation,
Mermaid companion render, week-over-week whatchanged diffs, and the live
`~/.claude/settings.json` <-> template sync check. Each returns
`(failures, total)` except `_settings_template_sync_check`, which returns a
third `skipped` count (it can find no live settings.json to compare against).
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple

from acceptance.shared import HOOKS, PYTHON, REPO, _Checker


def _system_map_coverage_check() -> Tuple[int, int]:
    """The system map must cover exactly the fleet, and the doc must agree.

    Guards the `/system-map` single source of truth (architecture/fleet.data.js)
    against drift, mechanically:
      1. every fleet repo (projects.toml − [global] architecture_ignore) appears
         on the map;
      2. no map entry is a stale/typo'd repo absent from the fleet;
      3. every mapped repo also appears in ARCHITECTURE.md (data ↔ doc agree).
    Returns the failure count.
    """
    import json
    import tomllib

    check = _Checker()

    arch = REPO / "architecture"
    toml = tomllib.loads((REPO / "hooks" / "projects.toml").read_text(encoding="utf-8"))
    ignore = set(toml.get("global", {}).get("architecture_ignore", []))
    fleet = {
        name for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
    } - ignore

    # fleet.data.js holds `window.FLEET = { ...strict JSON... };` — slice the object out.
    raw = (arch / "fleet.data.js").read_text(encoding="utf-8")
    data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    mapped = {
        e.get("repo", e["nm"])
        for section in ("governance", "enabling", "web", "pipe")
        for e in data.get(section, [])
    }

    missing = fleet - mapped
    stale = mapped - fleet
    check(f"system_map: every fleet repo is on the map (missing: {sorted(missing) or 'none'})", not missing)
    check(f"system_map: no stale map entries (stale: {sorted(stale) or 'none'})", not stale)

    doc = (arch / "ARCHITECTURE.md").read_text(encoding="utf-8")
    doc_missing = sorted(r for r in mapped if r not in doc)
    check(f"system_map: every mapped repo is in ARCHITECTURE.md (missing: {doc_missing or 'none'})", not doc_missing)

    return check.failures, check.total


_REGEN_HINT = (
    "regenerate + commit with `/system-map`, or directly:\n"
    "E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/build_data.py"
)


def _fleet_toml_check() -> Tuple[int, int, int]:
    """Per-repo `.fleet.toml` aggregation is fresh and can't silently go stale.

    Guards the self-describing map (`build_data.py`: residual + per-repo
    `.fleet.toml` → `fleet.data.js`). Split by *whose commit can fix a failure*
    (fleet-config#562):

    **Hard** — inputs this repo owns, so a fresh clone on any machine gets the
    same answer:
      1. fleet-config's own card in the committed `fleet.data.js` matches
         fleet-config's own committed `.fleet.toml` — the anti-staleness
         contract for the one card this repo can actually keep current.

    **Advisory** (reported, counted as *skipped*, never failed) — inputs that
    live in sibling checkouts, so no commit here can make them green:
      2. `fleet.data.js` is exactly what `build_data.py` regenerates;
      3. every repo in the residual's `_adopted` registry still carries a
         `.fleet.toml` on its committed default branch;
      4. every present `.fleet.toml` is a valid declaration.

    2-4 used to be hard, which meant a `.fleet.toml` commit in *any* sister repo
    turned this repo's gate red — blocking every `/issue-finish`, `/quick`, and
    `/issue-yolo` here, for a reason the author of the change could not see,
    until the weekly `/system-map` run regenerated the aggregate (observed on
    `main` at c70b88f: home-automation added a Modbus chip and this gate went
    red for two days). `/system-map` owns fleet-wide freshness — it regenerates
    and commits weekly, and `build_data.py --check` fails loud there.

    Returns (failures, total, skipped).
    """
    import importlib.util
    import tomllib

    check = _Checker()

    bd_path = REPO / ".claude" / "skills" / "system-map" / "build_data.py"
    spec = importlib.util.spec_from_file_location("system_map_build_data", bd_path)
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)

    committed_text = (REPO / "architecture" / "fleet.data.js").read_text(encoding="utf-8")

    # --- hard: fleet-config's own card, both sides committed in this repo ---
    own_detail = ""
    try:
        own_toml = bd.read_fleet_toml(REPO)
        if own_toml is None:
            own_ok, own_detail = False, "fleet-config has no committed .fleet.toml"
        else:
            section, own_card = bd.card_from_toml("fleet-config", tomllib.loads(own_toml))
            committed = json.loads(
                committed_text[committed_text.index("{"): committed_text.rindex("}") + 1]
            )
            mapped = [e for e in committed.get(section, []) if e.get("repo", e.get("nm")) == "fleet-config"]
            own_ok = mapped == [own_card]
            if not own_ok:
                own_detail = f"declared: {own_card}\nmapped:   {mapped}\n{_REGEN_HINT}"
    except Exception as exc:  # noqa: BLE001 - a malformed own declaration is our bug
        own_ok, own_detail = False, str(exc)
    check("fleet_toml: fleet-config's own card matches its own .fleet.toml", own_ok, own_detail)

    # --- advisory: everything below reads sibling repos' live checkouts ---
    try:
        fresh, regen_err = bd.regenerate() == committed_text, ""
    except Exception as exc:  # noqa: BLE001 - surface a malformed declaration cleanly
        fresh, regen_err = False, f" ({exc})"
    check.advisory(
        f"fleet_toml: fleet.data.js matches build_data.py output{regen_err}",
        fresh,
        f"a sibling repo's .fleet.toml moved ahead of the committed aggregate.\n{_REGEN_HINT}",
    )

    residual = bd.load_residual()
    repos = bd.fleet_repos()
    adopted = residual.get("_adopted", [])
    missing = [r for r in adopted if r not in repos or bd.read_fleet_toml(repos[r]) is None]
    check.advisory(
        f"fleet_toml: every adopted repo still has a .fleet.toml (missing: {sorted(missing) or 'none'})",
        not missing,
        "fix in the owning repo (or drop it from architecture/fleet.residual.json `_adopted`).",
    )

    invalid = []
    for name, repo_dir in sorted(repos.items()):
        text = bd.read_fleet_toml(repo_dir)
        if text is None:
            continue
        try:
            bd.card_from_toml(name, tomllib.loads(text))
        except Exception as exc:  # noqa: BLE001
            invalid.append(f"{name}: {exc}")
    check.advisory(
        f"fleet_toml: every present .fleet.toml is valid (invalid: {invalid or 'none'})",
        not invalid,
        "fix the declaration in the owning repo; schema: architecture/README.md.",
    )

    return check.failures, check.total, check.skipped


def _fleet_membership_drift_check() -> Tuple[int, int, int]:
    """The fleet on disk and the fleet in `projects.toml` are the same set (#640).

    `CLAUDE.md` makes that block the fleet-membership list — `fleet_repos()`
    reads it, so an omission silently narrows `/system-map`, `/config-map`,
    `/context-audit`'s cap gate and `chief_ops.py verify`, and leaves
    `notify_on_idle` pinging `[claude]` instead of naming the project. Nothing
    caught that: `local-llm-hub-lite` was worked by six `/cleanup-fleet-all`
    lanes while being invisible to every fleet report, because the list is
    maintained by hand and drift is silent by construction.

    So: every real fleet repo sitting next to this one must be declared, or
    named in `[global] architecture_ignore` — the documented "deliberately off
    the map" escape hatch. Hard, not `advisory`: unlike `_fleet_toml_check`'s
    fleet-wide half, the fix is a one-line commit *in this repo*, which is
    exactly the kind of failure a gate is for.

    Membership comes from the shared `fleet_repo_scan.iter_fleet_repos` rather
    than a fresh crawl, so the linked-worktree guard is inherited instead of
    re-derived — a sibling `<repo>-wt-<N>` is a full checkout and counting one
    as a repo is the same mistake #629 already fixed once.

    Finding no repos at all is its own state, not a pass: on a fresh clone or
    another machine there is no fleet next door to compare against, and a run
    that verified nothing must never read like one that verified everything
    (fleet-config#461, #501). Returns (failures, total, skipped).
    """
    import importlib.util
    import tomllib

    spec = importlib.util.spec_from_file_location(
        "fleet_repo_scan", REPO / "skills" / "_lib" / "fleet_repo_scan.py"
    )
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)  # type: ignore[union-attr]

    root = REPO.parent  # E:/automation — the fleet lives beside this checkout
    on_disk = {d.name for d in scan.iter_fleet_repos(root)}
    if not on_disk:
        print(f"SKIP  fleet_membership: no fleet repos found beside this checkout in {root} (skipped)")
        return 0, 0, 1

    toml = tomllib.loads((REPO / "hooks" / "projects.toml").read_text(encoding="utf-8"))
    declared = {
        name for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
    }
    ignored = set(toml.get("global", {}).get("architecture_ignore", []))

    check = _Checker()
    undeclared = sorted(on_disk - declared - ignored)
    check(
        f"fleet_membership: every repo in {root} is declared in projects.toml "
        f"(undeclared: {undeclared or 'none'})",
        not undeclared,
        "add each to hooks/projects.toml before the [global] block:\n"
        + "\n".join(f'[{r}]\ncwd_prefix = "{root.as_posix()}/{r}"' for r in undeclared)
        + "\n(then regenerate the maps per CLAUDE.md 'Adding a new fleet project'), "
        "or list it in [global] architecture_ignore to keep it off the map on purpose.",
    )

    return check.failures, check.total, check.skipped


def _advisory_semantics_check() -> Tuple[int, int]:
    """`_Checker.advisory` reports, it never gates (fleet-config#562).

    The scoping decision `_fleet_toml_check` rests on: a check whose inputs live
    in sibling checkouts may turn up drift, but must not make this repo's `main`
    unshippable. Pinned mechanically, because "advisory" is one careless
    `check(...)` away from being a hard failure again — and because the opposite
    mistake (swallowing drift into the passing state) is the false "done" the
    global CLAUDE.md forbids. Returns the failure count.
    """
    check = _Checker()

    def counts(drive) -> Tuple[int, int, int]:
        """Run one probe against a throwaway _Checker, swallowing its own
        OK/FAIL/SKIP line so a deliberate failing probe can't be mistaken for a
        real one in the gate output."""
        probe = _Checker()
        with contextlib.redirect_stdout(io.StringIO()):
            drive(probe)
        return probe.failures, probe.total, probe.skipped

    check("advisory: a pass counts toward Total like any other check",
          counts(lambda c: c.advisory("probe", True)) == (0, 1, 0))
    check("advisory: a failure counts as Skipped, never Failed",
          counts(lambda c: c.advisory("probe", False, "why it drifted")) == (0, 0, 1))
    check("advisory: an ordinary check still fails hard (the escape hatch isn't global)",
          counts(lambda c: c("probe", False)) == (1, 1, 0))

    src = Path(__file__).read_text(encoding="utf-8")
    body = src.split("def _fleet_toml_check", 1)[1].split("\ndef ", 1)[0]
    check("advisory: the three fleet-wide fleet_toml checks are still advisory",
          body.count("check.advisory(") == 3 and body.count("\n    check(") == 1)

    return check.failures, check.total


def _unattended_worktree_mandate_check() -> Tuple[int, int]:
    """Every unattended dispatch path must force worktree mode (#515, #525).

    A *running* app is not a claim holder, so an ordinary `acquire` hands
    machine-dispatched work `MODE=primary` in a repo whose primary checkout is
    being served live -- that is what broke the running launcher on 2026-07-30.
    Four skills carry the rule in prose, which a context purge or a well-meaning
    rewrite can quietly drop; this pins it. Checks the flag is named in each,
    and that `/issue-start` still keys on the launcher's own session variable
    rather than some re-derived heuristic. Returns the failure count.
    """
    check = _Checker()
    flag = "--force-worktree"

    for rel in (
        ".claude/workflows/cleanup-fleet-all.js",
        ".claude/skills/cleanup-fleet/SKILL.md",
        "skills/codebase-audit/SKILL.md",
        "skills/issue-start/SKILL.md",
    ):
        body = (REPO / rel).read_text(encoding="utf-8")
        check(f"worktree mandate: {rel} forces worktree mode", flag in body)

    issue_start = (REPO / "skills" / "issue-start" / "SKILL.md").read_text(encoding="utf-8")
    check(
        "worktree mandate: /issue-start keys the force on APP_LAUNCHER_SESSION_ID (#525)",
        "APP_LAUNCHER_SESSION_ID" in issue_start,
    )

    wc = (REPO / "skills" / "_lib" / "worktree_claim.py").read_text(encoding="utf-8")
    check(
        "worktree mandate: acquire actually implements --force-worktree",
        'print("MODE=worktree")' in wc and "force_worktree" in wc,
    )

    return check.failures, check.total


def _mermaid_check() -> Tuple[int, int]:
    """The Mermaid companion render (`render_mermaid.py`) can't silently go stale.

    Guards the text-native fleet map the same way `_fleet_toml_check` guards
    `fleet.data.js`:
      1. `system-map.mmd` is exactly what `render_mermaid.py` regenerates from
         the current `fleet.data.js` — a forgotten regen fails loud;
      2. the marked `<!-- system-map:mermaid:start -->…:end` block inside
         `global-CLAUDE.md` embeds that same flowchart body verbatim.
    Returns the failure count.
    """
    import importlib.util

    check = _Checker()

    rm_path = REPO / ".claude" / "skills" / "system-map" / "render_mermaid.py"
    spec = importlib.util.spec_from_file_location("system_map_render_mermaid", rm_path)
    rm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rm)

    data = rm.load_data((REPO / "architecture" / "fleet.data.js").read_text(encoding="utf-8"))
    rendered = rm.render(data)
    flowchart_body = rm.render_flowchart(data)

    committed = (REPO / "architecture" / "system-map.mmd").read_text(encoding="utf-8")
    check("mermaid: system-map.mmd matches render_mermaid.py output", rendered == committed)

    claude_md = (REPO / "global-CLAUDE.md").read_text(encoding="utf-8")
    check(
        "mermaid: global-CLAUDE.md fleet-map block matches the current flowchart",
        rm.CLAUDE_MD_START in claude_md and f"```mermaid\n{flowchart_body}```" in claude_md,
    )

    return check.failures, check.total


def _system_map_whatchanged_check() -> Tuple[int, int]:
    """The /system-map week-over-week diff (.claude/skills/system-map/whatchanged.py).

    Pure-logic guard on the diff that feeds the one-line Slack summary: added /
    removed repos are named, in-place edits are counted, a no-op week and a
    first run read sensibly. Returns the failure count.
    """
    import importlib.util

    check = _Checker()

    wc_path = REPO / ".claude" / "skills" / "system-map" / "whatchanged.py"
    spec = importlib.util.spec_from_file_location("system_map_whatchanged", wc_path)
    wc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wc)  # type: ignore[union-attr]

    prev = 'window.FLEET = {"web":[{"nm":"a","ds":"x"},{"nm":"b","ds":"y"}],"pipe":[{"nm":"c","ds":"z"}]};'
    # add d, remove b, edit a's description, c unchanged.
    cur = 'window.FLEET = {"web":[{"nm":"a","ds":"X2"},{"nm":"d","ds":"w"}],"pipe":[{"nm":"c","ds":"z"}]};'
    diff = wc.diff_fleet(prev, cur)
    check("system_map_whatchanged: detects an added repo", diff["added"] == ["d"])
    check("system_map_whatchanged: detects a removed repo", diff["removed"] == ["b"])
    check("system_map_whatchanged: counts edited cards, ignores unchanged", diff["updated"] == ["a"])

    # repo-keyed card (display name differs from repo) is keyed by `repo`.
    repo_prev = 'window.FLEET = {"web":[{"nm":"grocery","repo":"grocery-shopping-automation","ds":"x"}]};'
    repo_cur = 'window.FLEET = {"web":[]};'
    check("system_map_whatchanged: keys cards by repo-or-nm",
          wc.diff_fleet(repo_prev, repo_cur)["removed"] == ["grocery-shopping-automation"])

    check("system_map_whatchanged: format_line composes named adds/removes + count",
          wc.format_line(diff) == "+d, −b, 1 repo updated")
    check("system_map_whatchanged: empty diff reads 'no fleet changes'",
          wc.format_line({"added": [], "removed": [], "updated": []}) == "no fleet changes")
    check("system_map_whatchanged: no prior snapshot reads 'baseline'",
          wc.summarize(None, cur) == "baseline")

    return check.failures, check.total


def _config_map_check() -> Tuple[int, int, int]:
    """The /config-map data is fresh, and its week-over-week diff behaves.

    Guards the introspected config map (`.claude/skills/config-map`):
      1. **Advisory** — `config.data.js` is exactly what `build_data.py`
         regenerates. Same anti-staleness contract as `/system-map`, and the
         same scoping as `_fleet_toml_check`: `build_data.repo_skills()` /
         `coverage()` sweep every *sibling* repo's committed default branch, so
         a sister repo adding one `.claude/skills/` entry would otherwise turn
         this repo's gate red until the weekly `/config-map` run regenerated it
         (fleet-config#562). Reported, never failed; `/config-map` owns it.
      2. `whatchanged.py` pure-logic: adds/removes are named across every
         dimension (skills/hooks/matrix/conventions), edits are counted, repo
         keys collapse to a short label, and the no-op / first-run lines read
         sensibly. In-repo and deterministic — stays hard.
    Returns (failures, total, skipped).
    """
    import importlib.util

    check = _Checker()

    cm_dir = REPO / ".claude" / "skills" / "config-map"
    bd_spec = importlib.util.spec_from_file_location("config_map_build_data", cm_dir / "build_data.py")
    bd = importlib.util.module_from_spec(bd_spec)
    bd_spec.loader.exec_module(bd)  # type: ignore[union-attr]

    committed = (REPO / "architecture" / "config.data.js").read_text(encoding="utf-8")
    try:
        fresh = bd.regenerate() == committed
        regen_err = ""
    except Exception as exc:  # noqa: BLE001
        fresh, regen_err = False, f" ({exc})"
    check.advisory(
        f"config_map: config.data.js matches build_data.py output{regen_err}",
        fresh,
        "a sibling repo's committed skills/hooks moved ahead of the introspected snapshot.\n"
        "regenerate + commit with `/config-map`, or directly:\n"
        "E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/build_data.py",
    )

    wc_spec = importlib.util.spec_from_file_location("config_map_whatchanged", cm_dir / "whatchanged.py")
    wc = importlib.util.module_from_spec(wc_spec)
    wc_spec.loader.exec_module(wc)  # type: ignore[union-attr]

    prev = ('window.CONFIG = {"skills_universal":[{"nm":"a","ds":"x"},{"nm":"b","ds":"y"}],'
            '"hooks":[{"nm":"h1","ds":"z"}]};')
    # add skill c, remove skill b, edit a's description, hook h1 unchanged.
    cur = ('window.CONFIG = {"skills_universal":[{"nm":"a","ds":"X2"},{"nm":"c","ds":"w"}],'
           '"hooks":[{"nm":"h1","ds":"z"}]};')
    diff = wc.diff_config(prev, cur)
    check("config_map_whatchanged: detects an added entry", diff["added"] == ["skill:c"])
    check("config_map_whatchanged: detects a removed entry", diff["removed"] == ["skill:b"])
    check("config_map_whatchanged: counts edited entries, ignores unchanged", diff["updated"] == ["skill:a"])
    check("config_map_whatchanged: format_line composes named adds/removes + count",
          wc.format_line(diff) == "+c, −b, 1 updated")

    # repo-specific skills flatten to repo:<repo>/<item>; the label drops the path.
    rp = 'window.CONFIG = {"skills_repo":[{"repo":"life-os","items":["j1","j2"]}]};'
    rc = 'window.CONFIG = {"skills_repo":[{"repo":"life-os","items":["j1"]}]};'
    check("config_map_whatchanged: keys repo skills by path, labels by short name",
          wc.format_line(wc.diff_config(rp, rc)) == "−j2")

    check("config_map_whatchanged: empty diff reads 'no config changes'",
          wc.format_line({"added": [], "removed": [], "updated": []}) == "no config changes")
    check("config_map_whatchanged: no prior snapshot reads 'baseline'",
          wc.summarize(None, cur) == "baseline")

    return check.failures, check.total, check.skipped


def _readme_layout_check() -> Tuple[int, int]:
    """README's Layout tree really is the exhaustive inventory it reads as.

    fleet-config#565 (and #504 before it): the README's *prose* keeps up with
    each feature as it ships, the Layout block does not — so the two halves of
    one document ended up disagreeing about what exists (`agy/`,
    `copilot-hooks/`, and all three `session_state*` hooks were absent, and the
    count above the hook table was one short). A reader trusts that block as the
    file inventory, so a missing line reads as "this doesn't exist" rather than
    "this isn't listed" — which is worse than a plain omission. Three mechanical
    parts, so the next new directory or hook fails here instead of rotting:
      1. every top-level tracked directory is named in the Layout block;
      2. every `hooks/*.py` module is named in it;
      3. the "<N> hooks under `hooks/`" count matches the hook table's rows.
    Returns (failures, total).
    """
    check = _Checker()

    readme = (REPO / "README.md").read_text(encoding="utf-8")

    # The fenced tree under "## Layout", up to the next top-level heading.
    after = readme.split("\n## Layout\n", 1)[1]
    layout = after.split("\n## ", 1)[0]

    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    top_dirs = sorted({
        line.split("/", 1)[0]
        for line in tracked.stdout.splitlines()
        if "/" in line
    })
    missing_dirs = [d for d in top_dirs if f"{d}/" not in layout]
    check(
        f"readme_layout: every top-level tracked directory is in the Layout tree "
        f"(missing: {missing_dirs or 'none'})",
        not missing_dirs,
    )

    hook_modules = sorted(p.name for p in (REPO / "hooks").glob("*.py"))
    missing_hooks = [h for h in hook_modules if h not in layout]
    check(
        f"readme_layout: every hooks/*.py module is in the Layout tree "
        f"(missing: {missing_hooks or 'none'})",
        not missing_hooks,
    )

    # "18 hooks under `hooks/` that ..." must match the table it introduces.
    m = re.search(r"^(\d+) hooks under `hooks/`", readme, re.M)
    rows = len(re.findall(r"^\| `[a-z0-9_]+\.py` \|", readme, re.M))
    claimed = int(m.group(1)) if m else -1
    check(
        f"readme_layout: the hook count matches the hook table (claims {claimed}, table has {rows})",
        claimed == rows,
    )

    return check.failures, check.total


def _settings_template_sync_check() -> Tuple[int, int, int]:
    """Every hook wired in settings.template.json must also be wired in the live
    ~/.claude/settings.json.

    The live file is machine-local and NOT version-controlled (it carries
    permissions + secrets), so it can silently drift from the template — a hook
    can ship in the repo yet never actually run. This guard fails loudly when a
    template-wired `(event, hook)` is missing from the live file. Direction is
    template ⊆ live only: machine-local *extra* hooks are legitimate and don't
    fail. Skips gracefully (one line, exit 0) when the live file is absent, so
    it never breaks on a machine without it. Prints exactly one line either way
    — always one check, whether skipped or run — but a skip is its own state:
    it contributes to neither Total nor Failed, only to the separate Skipped
    counter, so a run that couldn't verify the live file never reads identical
    to one that actually verified it and passed (fleet-config#461, #501).
    """
    import re

    hook_re = re.compile(r"-Hook\s+(\w+)")

    def wired(path: Path) -> set[tuple[str, str]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        pairs: set[tuple[str, str]] = set()
        for event, blocks in data.get("hooks", {}).items():
            for block in blocks:
                for hook in block.get("hooks", []):
                    m = hook_re.search(hook.get("command", ""))
                    if m:
                        pairs.add((event, m.group(1)))
        return pairs

    live_path = Path.home() / ".claude" / "settings.json"
    if not live_path.exists():
        print("SKIP  settings_sync: no live ~/.claude/settings.json (skipped)")
        return 0, 0, 1

    template = wired(REPO / "settings.template.json")
    live = wired(live_path)
    missing = sorted(template - live)
    ok = not missing
    print(f"{'OK   ' if ok else 'FAIL '} settings_sync: template hooks all wired live "
          f"(missing: {missing or 'none'})")
    return (0 if ok else 1), 1, 0


