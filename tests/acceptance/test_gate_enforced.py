"""Tests for the gate-enforced map the weekly audits consult before scanning (fleet-config#1645).

Four layers:
  1. The map itself, pure -- every ratchet table has an entry, every bucket is a
     `/codebase-audit` label, the conventions rules match the vendored ratchet's, and the
     instruction rules are exactly the vendored instruction ratchet's `RULES`, all lint-tier.
  2. `in_instruction_scope`, pure -- the files the instruction ratchet scans.
  3. `read_gate` on throwaway git repos -- an adopted repo claims its checks and prompt rules,
     narrowed to what its own vendored copy declares; a non-adopted repo and an unreadable one
     (`unknown`) claim nothing, so the audits scan every check there.
  4. The consumers -- `dead_code_leads` skips vulture under `[dead_code_ratchet]` and scans
     when the gate is unknown; prompt-audit's lint drops the enforced rules only for an
     adopted, in-scope file and its digest labels them.

Lives under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_gate_enforced.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
sys.path.insert(0, str(REPO / ".claude" / "skills" / "prompt-audit"))
from dead_code import dead_code_leads as dcl  # noqa: E402
import prompt_audit as pa  # noqa: E402
from check_harness import CheckHarness  # noqa: E402
from gate_coverage import enforced, reader  # noqa: E402
from git_fixtures import make_upstream_and_clone, run_git  # noqa: E402
from prompt_audit import cli as pa_cli  # noqa: E402
from prompt_audit.digest import gate_digest_lines  # noqa: E402
from prompt_audit.inventory import Entry  # noqa: E402

_h = CheckHarness()
check = _h.check


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- 1. the map ----

_BUCKETS = {"duplication", "stale", "claude-md-drift", "maintainability", "slop", "bug", "documentation"}
check(set(enforced.CHECKS) == set(reader.RATCHETS), "CHECKS: one entry per ratchet table the reader knows")
check({b for pairs in enforced.CHECKS.values() for b, _ in pairs} | {b for b, _ in enforced.CONVENTIONS.values()}
      <= _BUCKETS, "CHECKS: every bucket is a /codebase-audit bucket label")
_conv = _load("scripts/conventions_ratchet.py")
check(set(enforced.CONVENTIONS) == set(_conv.RULES), f"CONVENTIONS: matches the vendored rules {_conv.RULES}")
_instr = _load("scripts/instruction_ratchet.py")
check(tuple(sorted(_instr.RULES)) == enforced.INSTRUCTION_RULES,
      f"INSTRUCTION_RULES: exactly the vendored instruction ratchet's RULES, got {sorted(_instr.RULES)}")
check(set(enforced.INSTRUCTION_RULES) <= set(pa.LINT_RULES) and not set(enforced.INSTRUCTION_RULES) & set(_instr.NOT_ENFORCED),
      "INSTRUCTION_RULES: all lint-tier prompt-audit rules, none the ratchet leaves to the audit")
_declared = set(enforced._RULE_KEY.findall((REPO / "scripts" / "instruction_ratchet.py").read_text(encoding="utf-8")))
check(_declared == set(_instr.RULES), f"_RULE_KEY: reads exactly the RULES keys from the vendored source, got {sorted(_declared)}")


# ---- 2. instruction-ratchet scope ----

_scope = {"skill_dirs": ["skills", ".claude/skills"], "extra_files": ["global-CLAUDE.md"], "rules": ["R-01"]}
for _rel, _want in (("CLAUDE.md", True), ("AGENTS.md", True), ("global-CLAUDE.md", True),
                    (".claude/rules/style.md", True), ("skills/x/SKILL.md", True), (".claude/skills/x/ref.md", True),
                    ("sub/CLAUDE.md", False), ("docs/guide.md", False), ("skills/README.md", False),
                    (".claude/rules/deep/x.md", False), ("other/x/SKILL.md", False)):
    check(enforced.in_instruction_scope(_rel, _scope) is _want, f"in_instruction_scope({_rel!r}) is {_want}")
check(enforced.prompt_rules_for({"state": "unknown", "prompt": _scope}, "CLAUDE.md") == [],
      "prompt_rules_for: an unknown gate enforces nothing")


# ---- 3. read_gate on real git repos ----

def _baseline(entries: list) -> str:
    return json.dumps({"version": 1, "entries": entries})


_INSTR_SRC = (REPO / "scripts" / "instruction_ratchet.py").read_text(encoding="utf-8")
_FULL_TOML = ("[static_ratchet]\nenabled = true\n[dead_code_ratchet]\nenabled = true\n"
              "[conventions_ratchet]\nenabled = true\nrules = [\"no-window\"]\n"
              "[instruction_ratchet]\nenabled = true\nskill_dirs = [\"skills\"]\n")


def _repo(root: Path, name: str, files: dict) -> Path:
    """A clone whose `origin/main` holds `files`; the working tree is left on a branch that disables everything."""
    base = root / name
    base.mkdir()
    upstream, work = make_upstream_and_clone(base, check)
    for rel, text in files.items():
        (upstream / rel).parent.mkdir(parents=True, exist_ok=True)
        (upstream / rel).write_text(text, encoding="utf-8", newline="\n")
        run_git(upstream, "add", rel, check=check)
    if files:
        run_git(upstream, "commit", "-q", "-m", "adopt", check=check)
        run_git(work, "fetch", "-q", "origin", check=check)
        run_git(work, "merge", "-q", "--ff-only", "origin/main", check=check)
    run_git(work, "checkout", "-q", "-b", "feature", check=check)
    (work / ".fleet.toml").write_text("[dead_code_ratchet]\nenabled = false\n", encoding="utf-8")
    return work


_tmp = Path(tempfile.mkdtemp(prefix="gate_enforced_"))
try:
    _py = "def dead_fn():\n    return 1\n"
    adopted = _repo(_tmp, "adopted", {".fleet.toml": _FULL_TOML, "scripts/instruction_ratchet.py": _INSTR_SRC,
                                      ".static-ratchet-baseline.json": _baseline([{"key": "dup:a"}]), "m.py": _py})
    _g = enforced.read_gate(str(adopted))
    _owned = {(c["ratchet"], c["bucket"]) for c in _g["checks"]}
    check(_g["state"] == "adopted" and {("static_ratchet", "duplication"), ("dead_code_ratchet", "stale"),
                                        ("conventions_ratchet:no-window", "claude-md-drift")} <= _owned,
          f"read_gate: an adopted repo claims its ratchets' checks and enabled conventions rules, got {_g}")
    check(not any(c["ratchet"] == "docs_ratchet" for c in _g["checks"]), "read_gate: a ratchet not enabled claims nothing")
    check(next(c["baselined"] for c in _g["checks"] if c["ratchet"] == "static_ratchet") == 1,
          "read_gate: each claim carries its ratchet's baselined debt")
    check(_g["prompt"]["rules"] == list(enforced.INSTRUCTION_RULES) and _g["prompt"]["skill_dirs"] == ["skills"],
          f"read_gate: the instruction ratchet's rules and declared skill_dirs, got {_g['prompt']}")
    check(enforced.enforces(_g, "dead_code_ratchet") and enforced.enforces(_g, "instruction_ratchet")
          and not enforced.enforces(_g, "docs_ratchet"), "enforces: true only for an enabled ratchet")
    _lines = enforced.report_lines(_g, "adopted")
    check(_lines[0].startswith("GATE=adopted|state=adopted") and any(
        ln.startswith("gate-enforced: stale: functions, classes and methods") for ln in _lines),
          f"report_lines: a GATE header and a gate-enforced line per check, got {_lines}")

    _old = _INSTR_SRC.replace('    "R-45": (', '    "R-99": (')
    older = _repo(_tmp, "older", {".fleet.toml": _FULL_TOML, "scripts/instruction_ratchet.py": _old})
    check("R-45" not in enforced.read_gate(str(older))["prompt"]["rules"],
          "read_gate: a rule the repo's own vendored copy does not declare stays with the audit")

    plain = _repo(_tmp, "plain", {"m.py": _py})
    _p = enforced.read_gate(str(plain))
    check(_p["state"] == "not_adopted" and not _p["checks"] and _p["prompt"] is None,
          f"read_gate: a repo with no .fleet.toml on origin claims nothing, got {_p}")

    broken = _repo(_tmp, "broken", {".fleet.toml": _FULL_TOML, "scripts/instruction_ratchet.py": _INSTR_SRC,
                                    ".static-ratchet-baseline.json": "{nope", "m.py": _py})
    _b = enforced.read_gate(str(broken))
    check(_b["state"] == "unknown" and "not valid JSON" in _b["reason"] and not _b["checks"] and _b["prompt"] is None,
          f"read_gate: an unreadable baseline is unknown and claims nothing, got {_b}")
    check(enforced.report_lines(_b, "broken")[0].endswith("|every check scanned"),
          "report_lines: an unknown gate says every check is scanned")
    unvendored = _repo(_tmp, "unvendored", {".fleet.toml": _FULL_TOML})
    _u = enforced.read_gate(str(unvendored))
    check(_u["state"] == "unknown" and "instruction_ratchet.py" in _u["reason"],
          f"read_gate: an enabled instruction ratchet with no vendored copy on origin is unknown, got {_u}")

    # ---- 4. consumers ----
    _d = dcl.leads(adopted)
    check(_d["state"] == "skipped" and "[dead_code_ratchet]" in _d["reason"] and _d["hits"] == [],
          f"dead_code_leads: the vulture pass is skipped under an enabled [dead_code_ratchet], got {_d}")
    check(dcl.main(["leads", str(adopted)]) == 0, "dead_code_leads: a skipped verdict exits 0")
    _dr = dcl.leads(adopted, with_ruff=True)["ruff"]
    check(_dr["state"] == "skipped", f"dead_code_leads: ruff is skipped under [static_ratchet] read from origin, got {_dr}")
    if importlib.util.find_spec("vulture") is not None:
        for _repo_path, _label in ((plain, "not adopted"), (broken, "unknown")):
            _v = dcl.leads(_repo_path)
            check(_v["state"] == "ok" and _v["gate"]["state"] in ("not_adopted", "unknown")
                  and "dead_fn" in {h["name"] for h in _v["hits"]},
                  f"dead_code_leads: a {_label} gate is scanned, got {_v['state']} {_v['reason']}")

    def _entry(repo_dir: Path, name: str, rel: str) -> Entry:
        return Entry(key=f"{name}/{rel}", path=repo_dir / rel, repo=name, kind="claude-md", repo_dir=repo_dir)

    _gates: dict = {}
    check(pa_cli._gate_rules(_entry(adopted, "adopted", "CLAUDE.md"), _gates) == list(enforced.INSTRUCTION_RULES),
          "prompt-audit lint: an adopted repo's in-scope file drops the enforced rules")
    check(pa_cli._gate_rules(_entry(adopted, "adopted", "docs/guide.md"), _gates) == [],
          "prompt-audit lint: a file outside the ratchet's scope keeps every rule")
    check(pa_cli._gate_rules(_entry(broken, "broken", "CLAUDE.md"), _gates) == []
          and pa_cli._gate_rules(_entry(plain, "plain", "CLAUDE.md"), _gates) == [],
          "prompt-audit lint: an unknown or non-adopted repo keeps every rule")
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

_dig = gate_digest_lines(["GATE=a|state=adopted|enforced=R-01,R-02|baselined=3", "GATE=b|state=not_adopted|enforced=none",
                          "GATE=c|state=unknown|reason=bad baseline|enforced=none"])
check(_dig[0].startswith("**Gate-enforced:** 1 of 3 repos") and "3 baselined hits remain" in _dig[0]
      and "- `a` R-01,R-02 (3 baselined)" in _dig and any(ln.startswith("- `c` unknown (bad baseline)") for ln in _dig),
      f"gate_digest_lines: adopted repos labelled with their debt, unknown ones named as fully linted, got {_dig}")
check(gate_digest_lines(None) == ["**Gate-enforced:** not-checked"], "gate_digest_lines: no GATE lines is not-checked")
_body, _ = pa.render_digest({"date": "2026-10-25", "scan_ran": False, "gate": ["GATE=a|state=adopted|enforced=R-01|baselined=0"]}, {})
check("**Gate-enforced:** 1 of 1 repos" in _body, "render_digest: the digest carries the gate-enforced section")

_h.report_and_exit("test_gate_enforced")
