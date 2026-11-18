"""The week-over-week "what changed" diffs of `/system-map` and `/config-map` (their `whatchanged.py`).

Pure-logic guards on the diffs that feed each skill's one-line notify summary: added and removed
entries are named, in-place edits are counted, a no-op week and a first run read sensibly. Both run
inside `architecture_guards._config_map_check`, which owns the config-map freshness check beside them.
"""
from __future__ import annotations

from pathlib import Path

from acceptance.shared import REPO, _Checker


def _system_map_whatchanged_cases(check: _Checker) -> None:
    """The /system-map week-over-week diff (.claude/skills/system-map/whatchanged.py).

    Pure-logic guard on the diff that feeds the one-line notify summary: added /
    removed repos are named, in-place edits are counted, a no-op week and a
    first run read sensibly. Runs inside `_config_map_check`, beside the
    config-map diff it mirrors.
    """
    import importlib.util

    wc_path = REPO / ".claude" / "skills" / "system-map" / "whatchanged.py"
    spec = importlib.util.spec_from_file_location("system_map_whatchanged", wc_path)
    wc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wc)  # type: ignore[union-attr]

    prev = 'window.FLEET = {"web":[{"nm":"a","ds":"x"},{"nm":"b","ds":"y"}],"pipe":[{"nm":"c","ds":"z"}]};'
    # add d, remove b, edit a's description, c unchanged.
    cur = 'window.FLEET = {"web":[{"nm":"a","ds":"X2"},{"nm":"d","ds":"w"}],"pipe":[{"nm":"c","ds":"z"}]};'
    diff = wc.snapshot_diff.diff_entries(wc.parse_fleet(prev), wc.parse_fleet(cur))
    check("system_map_whatchanged: detects an added repo", diff["added"] == ["d"])
    check("system_map_whatchanged: detects a removed repo", diff["removed"] == ["b"])
    check("system_map_whatchanged: counts edited cards, ignores unchanged", diff["updated"] == ["a"])

    # repo-keyed card (display name differs from repo) is keyed by `repo`.
    repo_prev = 'window.FLEET = {"web":[{"nm":"grocery","repo":"grocery-shopping-automation","ds":"x"}]};'
    repo_cur = 'window.FLEET = {"web":[]};'
    check("system_map_whatchanged: keys cards by repo-or-nm",
          wc.snapshot_diff.diff_entries(wc.parse_fleet(repo_prev), wc.parse_fleet(repo_cur))["removed"]
          == ["grocery-shopping-automation"])

    check("system_map_whatchanged: format_line composes named adds/removes + count",
          wc.format_line(diff) == "+d, −b, 1 repo updated")
    check("system_map_whatchanged: empty diff reads 'no fleet changes'",
          wc.format_line({"added": [], "removed": [], "updated": []}) == "no fleet changes")
    check("system_map_whatchanged: no prior snapshot reads 'baseline'",
          wc.summarize(None, cur) == "baseline")


def _config_map_whatchanged_cases(check: _Checker, cm_dir: Path) -> None:
    """The /config-map week-over-week diff (`whatchanged.py` beside `build_data.py`)."""
    import importlib.util

    wc_spec = importlib.util.spec_from_file_location("config_map_whatchanged", cm_dir / "whatchanged.py")
    wc = importlib.util.module_from_spec(wc_spec)
    wc_spec.loader.exec_module(wc)  # type: ignore[union-attr]

    prev = ('window.CONFIG = {"skills_universal":[{"nm":"a","ds":"x"},{"nm":"b","ds":"y"}],'
            '"hooks":[{"nm":"h1","ds":"z"}]};')
    # add skill c, remove skill b, edit a's description, hook h1 unchanged.
    cur = ('window.CONFIG = {"skills_universal":[{"nm":"a","ds":"X2"},{"nm":"c","ds":"w"}],'
           '"hooks":[{"nm":"h1","ds":"z"}]};')
    diff = wc.snapshot_diff.diff_entries(wc.parse_config(prev), wc.parse_config(cur))
    check("config_map_whatchanged: detects an added entry", diff["added"] == ["skill:c"])
    check("config_map_whatchanged: detects a removed entry", diff["removed"] == ["skill:b"])
    check("config_map_whatchanged: counts edited entries, ignores unchanged", diff["updated"] == ["skill:a"])
    check("config_map_whatchanged: format_line composes named adds/removes + count",
          wc.format_line(diff) == "+c, −b, 1 updated")

    # repo-specific skills flatten to repo:<repo>/<item>; the label drops the path.
    rp = 'window.CONFIG = {"skills_repo":[{"repo":"life-os","items":["j1","j2"]}]};'
    rc = 'window.CONFIG = {"skills_repo":[{"repo":"life-os","items":["j1"]}]};'
    check("config_map_whatchanged: keys repo skills by path, labels by short name",
          wc.format_line(wc.snapshot_diff.diff_entries(wc.parse_config(rp), wc.parse_config(rc))) == "−j2")

    check("config_map_whatchanged: empty diff reads 'no config changes'",
          wc.format_line({"added": [], "removed": [], "updated": []}) == "no config changes")
    check("config_map_whatchanged: no prior snapshot reads 'baseline'",
          wc.summarize(None, cur) == "baseline")
