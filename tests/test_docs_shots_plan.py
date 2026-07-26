"""Unit tests for the pure logic in skills/_lib/docs_shots_plan.py (fleet-config#93).

No live git — these exercise manifest discovery, diff-intersection against
`source_globs`, the unmapped-surface heuristic, and the README-marker check.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/test_docs_shots_plan.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "_lib"))
import docs_shots_plan as p  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "_lib"))
from check_harness import CheckHarness  # noqa: E402

_h = CheckHarness()
check = _h.check


MANIFEST = {
    "features": {
        "reporting": {"source_globs": ["app/app.py", "app/tab_reporting.py"]},
        "newsletter": {"source_globs": ["app/app.py", "app/tab_newsletter.py"]},
        "engagement": {"source_globs": ["app/tab_engagement.py", "engagement/ui.py"]},
    }
}


# ---- discovery ----

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    check(p.find_manifest_path(root) is None, "no manifest -> None (silent no-op)")
    (root / "docs" / "screenshots").mkdir(parents=True)
    manifest_path = root / "docs" / "screenshots" / "manifest.json"
    manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    found = p.find_manifest_path(root)
    check(found == manifest_path, "manifest found at the conventional path")
    check(p.load_manifest(found) == MANIFEST, "manifest loads back byte-for-byte as JSON")


# ---- feature_globs ----

globs = p.feature_globs(MANIFEST)
check(globs["reporting"] == ["app/app.py", "app/tab_reporting.py"], "feature_globs preserves order")
check(globs["engagement"] == ["app/tab_engagement.py", "engagement/ui.py"], "multi-dir feature globs kept")


# ---- stale_features ----

stale = p.stale_features(["app/tab_reporting.py"], MANIFEST)
check(list(stale.keys()) == ["reporting"], "only the touched feature is in the stale set")
check(stale["reporting"] == ["app/tab_reporting.py"], "matched file recorded")

stale_shared = p.stale_features(["app/app.py"], MANIFEST)
check(set(stale_shared.keys()) == {"reporting", "newsletter"},
      "a shared source file marks every feature that declares it stale")

check(p.stale_features(["README.md"], MANIFEST) == {}, "an untouched-surface change yields no stale features")
check(p.stale_features([], MANIFEST) == {}, "no changed files -> no stale features")

no_globs_manifest = {"features": {"x": {"source_globs": []}}}
check(p.stale_features(["app/app.py"], no_globs_manifest) == {},
      "a feature with empty source_globs never matches anything")


# ---- unmapped_changed_files ----

unmapped = p.unmapped_changed_files(
    ["app/tab_reporting.py", "app/tab_brand_new.py", "CLAUDE.md", "docs/other.md"], MANIFEST
)
check(unmapped == ["app/tab_brand_new.py"],
      "only a covered-dir file with no matching feature is flagged")

check(p.unmapped_changed_files(["app/tab_reporting.py"], MANIFEST) == [],
      "a file already matched by a feature is never also flagged as unmapped")

check(p.unmapped_changed_files(["CLAUDE.md", "README.md"], MANIFEST) == [],
      "files outside every manifest-covered directory are never flagged")

check(p.unmapped_changed_files(["app/x.py"], {"features": {}}) == [],
      "an empty manifest (no covered dirs at all) flags nothing")


# ---- readme_has_markers ----

check(p.readme_has_markers("intro\n<!-- docs-shots:start -->\nblock\n<!-- docs-shots:end -->\noutro"),
      "both markers present -> True")
check(not p.readme_has_markers("intro\n<!-- docs-shots:start -->\nblock (no end marker)"),
      "missing end marker -> False")
check(not p.readme_has_markers("no markers here at all"), "no markers -> False")


# ---- _format_stale (CLI output shape) ----

check(p._format_stale({}) == "", "empty stale set formats to an empty string")
check(p._format_stale({"reporting": ["app/app.py"]}) == "reporting:app/app.py",
      "single feature, single file")
check(
    p._format_stale({"reporting": ["app/app.py", "app/tab_reporting.py"], "newsletter": ["app/app.py"]})
    == "reporting:app/app.py|app/tab_reporting.py;newsletter:app/app.py",
    "multiple features and files use | and ; separators",
)


_h.report_and_exit("docs_shots_plan")
