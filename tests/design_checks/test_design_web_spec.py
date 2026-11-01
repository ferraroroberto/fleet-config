"""Checks on design.web.md and design.web.rubric.toml, the web-page design standard.

The spec splits a fleet-wide contract from a per-site brand, and the split only
holds if no brand value creeps into the contract. These checks hold it: the
contract half and the front matter carry no colour value, the brand half
carries the worked example, and every token the contract names is in its
table. The page rubric loads through `design_review.rubric` (the schema
`design.rubric.toml` uses), every `threshold_token` and `[params]` token
resolves from the spec's front matter, and the install symlink and the global
pointer are both in place so every session reaches the file.

Pure file reads, no browser and no network.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/design_checks/test_design_web_spec.py`  (also invoked by tests/run_acceptance.py)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))

from check_harness import CheckHarness  # noqa: E402
from design_lint.spec import parse_spec  # noqa: E402
from design_review import rubric as rb  # noqa: E402

_h = CheckHarness()
check = _h.check

SPEC = REPO / "design.web.md"
RUBRIC = REPO / "design.web.rubric.toml"
CONTRACT_HEADING = "# Part one: the contract"
BRAND_HEADING = "# Part two: the brand"
VERIFY_HEADING = "# Verification and sources"
HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
CONTRACT_TOKENS = (
    "--paper", "--ink", "--ink-2", "--line", "--line-strong", "--accent", "--footer-band",
    "--font", "--fs-title", "--fs-h2", "--fs-h3", "--fs-lead", "--fs-body", "--fs-card",
    "--fs-small", "--fs-label", "--gutter", "--gap", "--section", "--radius", "--frame",
    "--control", "--header", "--content", "--measure", "--img-feature", "--img-tile", "--img-thumb",
)
REQUIRED_KEYS = (
    "viewports.phone", "viewports.tablet", "viewports.desktop",
    "type.body-min", "contrast.text", "targets.hit-min",
    "performance.lcp", "performance.cls", "performance.inp", "performance.lighthouse-min",
    "seo.title-max", "seo.description-max", "images.social-card",
)
PAGE_TYPES = ("landing", "listing", "article", "product", "utility")
REQUIRED_CATEGORIES = ("hierarchy", "readability", "touch", "a11y", "trust", "seo", "performance")

text = SPEC.read_text(encoding="utf-8")
check(text.startswith("---\n") or text.startswith("---\r\n"), "design.web.md opens with front matter")
parts = text.split("---", 2)
front = parts[1] if len(parts) == 3 else ""
body = parts[2] if len(parts) == 3 else ""

for heading in (CONTRACT_HEADING, BRAND_HEADING, VERIFY_HEADING):
    check(heading in body, f"design.web.md has the heading {heading!r}")
i_contract, i_brand, i_verify = (body.find(h) for h in (CONTRACT_HEADING, BRAND_HEADING, VERIFY_HEADING))
check(-1 < i_contract < i_brand < i_verify, "contract, then brand, then verification, in that order")
contract = body[i_contract:i_brand]
brand = body[i_brand:i_verify]

check(not HEX.search(front), f"the front matter holds no colour value: {HEX.findall(front)}")
check(not HEX.search(contract), f"the contract half holds no colour value: {HEX.findall(contract)}")
check("Example brand" in brand and "#ffcc00" in brand, "the brand half carries the worked example's values")
check("Poppins" not in contract and "Poppins" not in front, "the example brand's typeface is named only in the brand half")
for token in CONTRACT_TOKENS:
    check(f"`{token}`" in contract, f"the contract's token table names {token}")

spec = parse_spec(text)
for key in REQUIRED_KEYS:
    check(key in spec, f"the front matter declares {key}")
for page_type in PAGE_TYPES:
    check(f"budgets.{page_type}.bytes" in spec and f"budgets.{page_type}.requests" in spec,
          f"the front matter budgets the {page_type} page type")

try:
    rubric = rb.load_rubric(RUBRIC)
except rb.RubricError as exc:
    rubric = None
    check(False, f"design.web.rubric.toml loads: {exc}")
if rubric is not None:
    check(all(r.id.startswith("WEB-") for r in rubric.rules), "every page-rubric rule id starts with WEB-")
    for category in REQUIRED_CATEGORIES:
        check(category in rubric.categories, f"the page rubric has rules in {category}")
    check(len(rubric.judgment) > 0, "the page rubric carries a judgment checklist")
    check(all(r.mockup == "none" for r in rubric.rules), "page-rubric rules name no app mock-up template")
    for rule in rubric.rules:
        if rule.threshold_token:
            resolved = rb.resolve_threshold(rule, spec)
            check(str(resolved["source"]).startswith("spec:"),
                  f"{rule.id}: threshold_token {rule.threshold_token} resolves from design.web.md")
    for name, entry in rubric.params.items():
        check(entry.get("token") in spec, f"[params.{name}] token {entry.get('token')} resolves from design.web.md")

install = (REPO / "install.ps1").read_text(encoding="utf-8")
check(re.search(r"source\s*=\s*'design\.web\.md';\s*target\s*=\s*'design\.web\.md'", install) is not None,
      "install.ps1 links design.web.md into ~/.claude")
check("~/.claude/design.web.md" in (REPO / "global-CLAUDE.md").read_text(encoding="utf-8"),
      "global-CLAUDE.md points web-page UI work at ~/.claude/design.web.md")

_h.report_and_exit("test_design_web_spec")
