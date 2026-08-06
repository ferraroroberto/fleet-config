"""Architecture / fleet-map freshness guards (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (b) --
`/system-map` and `/config-map`'s coverage, `.fleet.toml` aggregation,
Mermaid companion render, week-over-week whatchanged diffs, and the live
`~/.claude/settings.json` <-> template sync check. Each returns
`(failures, total)` except `_settings_template_sync_check`, which returns a
third `skipped` count (it can find no live settings.json to compare against).
"""
from __future__ import annotations

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


def _fleet_toml_check() -> Tuple[int, int]:
    """Per-repo `.fleet.toml` aggregation is fresh and can't silently go stale.

    Guards the self-describing map (`build_data.py`: residual + per-repo
    `.fleet.toml` → `fleet.data.js`):
      1. `fleet.data.js` is exactly what `build_data.py` regenerates — a forgotten
         regen, a hand-edit, or an un-committed `.fleet.toml` change fails loud;
      2. every repo in the residual's `_adopted` registry still carries a
         `.fleet.toml` on its committed default branch — deleting one (which would
         silently revert to the central fallback) fails loud;
      3. every present `.fleet.toml` is a valid declaration (parses, `layer` in
         the enum, required fields set).
    Returns the failure count.
    """
    import importlib.util
    import tomllib

    check = _Checker()

    bd_path = REPO / ".claude" / "skills" / "system-map" / "build_data.py"
    spec = importlib.util.spec_from_file_location("system_map_build_data", bd_path)
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)

    committed = (REPO / "architecture" / "fleet.data.js").read_text(encoding="utf-8")
    try:
        fresh = bd.regenerate() == committed
        regen_err = ""
    except Exception as exc:  # noqa: BLE001 - surface a malformed declaration cleanly
        fresh, regen_err = False, f" ({exc})"
    check(f"fleet_toml: fleet.data.js matches build_data.py output{regen_err}", fresh)

    residual = bd.load_residual()
    repos = bd.fleet_repos()
    adopted = residual.get("_adopted", [])
    missing = [r for r in adopted if r not in repos or bd.read_fleet_toml(repos[r]) is None]
    check(f"fleet_toml: every adopted repo still has a .fleet.toml (missing: {sorted(missing) or 'none'})", not missing)

    invalid = []
    for name, repo_dir in sorted(repos.items()):
        text = bd.read_fleet_toml(repo_dir)
        if text is None:
            continue
        try:
            bd.card_from_toml(name, tomllib.loads(text))
        except Exception as exc:  # noqa: BLE001
            invalid.append(f"{name}: {exc}")
    check(f"fleet_toml: every present .fleet.toml is valid (invalid: {invalid or 'none'})", not invalid)

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


def _config_map_check() -> Tuple[int, int]:
    """The /config-map data is fresh, and its week-over-week diff behaves.

    Guards the introspected config map (`.claude/skills/config-map`):
      1. `config.data.js` is exactly what `build_data.py` regenerates — a forgotten
         regen, a hand-edit, a new skill/hook, or a re-wired `install.ps1` link
         fails loud (same anti-staleness contract as `/system-map`);
      2. `whatchanged.py` pure-logic: adds/removes are named across every
         dimension (skills/hooks/matrix/conventions), edits are counted, repo
         keys collapse to a short label, and the no-op / first-run lines read
         sensibly.
    Returns the failure count.
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
    check(f"config_map: config.data.js matches build_data.py output{regen_err}", fresh)

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

    return check.failures, check.total


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


