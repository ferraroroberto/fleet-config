"""Tests for the structural check's scope and its scaffold promotion (fleet-config#1619).

Two layers, both pure (no `gh`, no network, no git):
  1. `structural.scope` -- `Structural-scope:` parsing, slug normalisation, the scaffold
     catalog read (an unreadable one is `unknown`, never a pass), scaffold-owned checks,
     a missing scope failing with `no-scope`, and `not encodable` needing no scope.
  2. `structural.promotion` -- marker identity, dedup planning (create, comment on open,
     comment-only on closed, two markers for one name), and `promote` against a fake `gh`
     that records every call: one issue per name, never a reopen, `unknown` on a `gh` failure.

The end-to-end gate on real git branches lives in tests/test_audit_issue.py. This file is
under tests/acceptance/ because the flat tests/ directory is baselined in
`.structure-ratchet-baseline.json` and takes no new module.

Run: `E:/automation/fleet-config/.venv/Scripts/python.exe tests/acceptance/test_structural_scope.py`  (also invoked by tests/run_acceptance.py)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "skills" / "_lib"))
sys.path.insert(0, str(REPO / "tests" / "_lib"))
from check_harness import CheckHarness  # noqa: E402
from structural import promotion, scope  # noqa: E402

_h = CheckHarness()
check = _h.check

_LONG = "the class is runtime-only behaviour that no static scan can see"
_WHY = "only this repo renders its own legacy report tables by hand"
_TOML = '[components]\ngeometry = { src = "tests/e2e/_geometry.py" }\nnav = { src = "app/webapp/static/_vendored/nav" }\n'
_RULES = 'RULES = ("no-window", "logging-not-print")\nTEST_RULES = frozenset({"no-window"})\n'
_CATALOG = scope.parse_catalog(_TOML, _RULES)


# ---- 1. scope ----

check(scope.slugify("NoWindow") == scope.slugify("no_window") == scope.slugify(" no window ") == "no-window",
      "slugify: camel, snake and spaced names all normalise to kebab-case")
check(scope.slugify("NoRawSQL2Calls") == "no-raw-sql2-calls" and scope.slugify("--") == "",
      f"slugify: digits stay attached and an all-punctuation name is empty, got {scope.slugify('NoRawSQL2Calls')!r}")

check(scope.parse_scope("Structural-scope: generalizable: NoBareExcept\n") == {"scope": "generalizable", "name": "no-bare-except"},
      "parse_scope: a generalizable line carries the slugged rule name")
check(scope.parse_scope(f"structural-scope: Repo-Specific: {_WHY}") == {"scope": "repo-specific", "why": _WHY},
      "parse_scope: a repo-specific line with a real reason, case-insensitive")
check(scope.parse_scope("Structural-scope: repo-specific: too short") is None, "parse_scope: a short repo-specific reason is no scope")
check(scope.parse_scope("Structural-scope: generalizable: every repo should ban print") is None,
      "parse_scope: prose is not a rule name")
check(scope.parse_scope("Structural-scope: maybe: something") is None and scope.parse_scope("") is None,
      "parse_scope: an unknown kind or no line is no scope")
check(scope.parse_scope("Structural-scope: generalizable: bad name\nStructural-scope: generalizable: `no-print`")
      == {"scope": "generalizable", "name": "no-print"}, "parse_scope: the first valid line wins")

check(_CATALOG == {"components": ["app/webapp/static/_vendored/nav", "tests/e2e/_geometry.py"], "rules": ["no-window", "logging-not-print"]},
      f"parse_catalog: component srcs and the RULES tuple, got {_CATALOG!r}")
check(scope.parse_catalog(None, _RULES) is None, "parse_catalog: no .fleet.toml is an unknown catalog")
check(scope.parse_catalog("[components\n", _RULES) is None, "parse_catalog: unparseable TOML is an unknown catalog")
check(scope.parse_catalog("[e2e]\n", _RULES) is None, "parse_catalog: no [components] table is an unknown catalog")
check(scope.parse_catalog(_TOML, "RULES = (\n") is None and scope.parse_catalog(_TOML, "X = 1\n") is None,
      "parse_catalog: an unparseable or RULES-less conventions script is an unknown catalog")

_ON = '[conventions_ratchet]\nenabled = true\nrules = ["no-window", "logging-not-print"]\n'
check(scope.rules_switched_on(None, _ON) == {"no-window", "logging-not-print"}, "rules_switched_on: every rule of a new table")
check(scope.rules_switched_on('[conventions_ratchet]\nenabled = true\nrules = ["no-window"]\n', _ON) == {"logging-not-print"},
      "rules_switched_on: only the rules the branch added")
check(scope.rules_switched_on(None, _ON.replace("true", "false")) == set(), "rules_switched_on: a disabled table switches nothing on")

check(scope.scaffold_owned("app/webapp/static/_vendored/nav/nav.js", _CATALOG, set()),
      "scaffold_owned: a path under a published component directory")
check(not scope.scaffold_owned("app/webapp/static/_vendored/navigator.js", _CATALOG, set()),
      "scaffold_owned: a sibling path that only shares a prefix is not the component")
check(scope.scaffold_owned(".fleet.toml", _CATALOG, {"no-window"}), "scaffold_owned: switching on an existing rule")
check(not scope.scaffold_owned(".fleet.toml", _CATALOG, {"no-raw-sql"}), "scaffold_owned: a rule the scaffold does not have is not owned")
check(not scope.scaffold_owned("tests/test_x.py", _CATALOG, {"no-window"}),
      "scaffold_owned: a switched-on rule does not make an unrelated check owned")


def _v(msgs: str, paths: list[str], catalog=_CATALOG, switched=frozenset()) -> dict:
    return scope.structural_verdict("recurring", msgs, paths, catalog, switched)


_DECL = "fix: x\n\nStructural-check: tests/test_x.py\n"
check(_v(_DECL, ["tests/test_x.py"]) == {"state": "fail", "reason": "no-scope", "kind": "check", "path": "tests/test_x.py"},
      "structural_verdict: a check with no scope line fails no-scope")
check(_v(_DECL + "Structural-scope: generalizable: NoX", ["tests/test_x.py"])
      == {"state": "pass", "kind": "check", "path": "tests/test_x.py", "scope": "generalizable", "name": "no-x"},
      "structural_verdict: a generalizable check passes with its name")
check(_v(_DECL + f"Structural-scope: repo-specific: {_WHY}", ["tests/test_x.py"])["scope"] == "repo-specific",
      "structural_verdict: a repo-specific check passes")
check(_v(_DECL + "Structural-scope: generalizable: NoX", ["tests/test_x.py"], catalog=None)
      == {"state": "unknown", "reason": "catalog-unknown", "scope": "unknown", "kind": "check", "path": "tests/test_x.py"},
      "structural_verdict: an unreadable catalog is unknown even with a declared scope")
check(_v("Structural-check: tests/e2e/_geometry.py", ["tests/e2e/_geometry.py"])["scope"] == "scaffold-owned",
      "structural_verdict: a published component needs no scope line")
check(_v(f"Structural-check: not encodable: {_LONG}", [], catalog=None) == {"state": "pass", "kind": "not-encodable", "scope": "n/a"},
      "structural_verdict: not encodable is scope n/a and needs no catalog")
check(scope.structural_verdict(None, _DECL, ["tests/test_x.py"]) == {"state": "n/a"}, "structural_verdict: no recurrence is n/a")
check(scope.verdict_line({"state": "pass", "kind": "check", "path": "a.py", "scope": "generalizable", "name": "no-x"})
      == "STRUCTURAL=pass KIND=check PATH=a.py SCOPE=generalizable NAME=no-x", "verdict_line: SCOPE= and NAME= on the STRUCTURAL= line")
check(scope.verdict_line({"state": "fail", "reason": "no-declaration"}) == "STRUCTURAL=fail REASON=no-declaration",
      "verdict_line: a fail keeps its old shape")


# ---- 2. promotion ----

def _issue(n: int, slug: str, state: str = "OPEN", reason: str = "") -> dict:
    return {"number": n, "state": state, "stateReason": reason, "url": f"u/{n}",
            "body": f"\n{promotion.marker(slug)}\nrest"}


check(promotion.marker_name(_issue(1, "no-x")["body"]) == "no-x", "marker_name: the marker on the first non-blank line")
check(promotion.marker_name("intro\n" + promotion.marker("no-x")) is None, "marker_name: a marker below prose is not identity")
check(promotion.parse_repo_pr("photo-ocr#45") == ("ferraroroberto/photo-ocr", 45)
      and promotion.parse_repo_pr("ferraroroberto/photo-ocr#45") == ("ferraroroberto/photo-ocr", 45),
      "parse_repo_pr: bare and owner-qualified forms")

check(promotion.plan([_issue(3, "other")], "no-x") == {"action": "create"}, "plan: no marker match creates")
check(promotion.plan([_issue(4, "no-x")], "no-x") == {"action": "comment", "number": 4, "closed": False, "completed": False},
      "plan: an open match is commented")
check(promotion.plan([_issue(4, "no-x", "CLOSED", "COMPLETED")], "no-x")
      == {"action": "comment", "number": 4, "closed": True, "completed": True}, "plan: a completed close is commented, not reopened")
check(promotion.plan([_issue(4, "no-x", "CLOSED", "NOT_PLANNED")], "no-x")["completed"] is False,
      "plan: a not-planned close is commented without the adopt note")
check(promotion.plan([_issue(9, "no-x"), _issue(5, "no-x"), _issue(2, "no-x", "CLOSED", "COMPLETED")], "no-x")["number"] == 5,
      "plan: two markers for one name -> the lowest open issue, never a new one")


class FakeGh:
    def __init__(self, issues: list[dict], fail_on: str | None = None):
        self.issues, self.fail_on, self.calls = issues, fail_on, []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if self.fail_on and args[:2] == ["issue", self.fail_on]:
            raise SystemExit(f"gh {' '.join(args[:2])} failed (exit 1)")
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        return "https://github.com/ferraroroberto/project-scaffolding/issues/" + ("900" if args[1] == "create" else "4#c")


_gh = FakeGh([])
_r = promotion.promote("NoRawSql", "photo-ocr#45", "tests/test_no_raw_sql.py", _gh)
_create = _gh.calls[-1]
check(_r["state"] == "created" and _create[:2] == ["issue", "create"], f"promote: no match creates, got {_r!r}")
check(_create[_create.index("--repo") + 1] == promotion.SCAFFOLD_REPO
      and [_create[i + 1] for i, a in enumerate(_create) if a == "--label"] == ["enhancement", "structural-promotion"]
      and "@me" in _create, "promote: created in the scaffold with both labels, self-assigned")
_body = _create[_create.index("--body") + 1]
check(_body.splitlines()[0] == promotion.marker("no-raw-sql") and "ferraroroberto/photo-ocr#45" in _body
      and "`tests/test_no_raw_sql.py`" in _body, "promote: the body leads with the marker and names repo, PR and path")
check(promotion.result_line(_r) == f"PROMOTION=created URL={_r['url']}", "result_line: created")

_gh = FakeGh([_issue(4, "no-raw-sql")])
_r = promotion.promote("no_raw_sql", "reporting#13", "tests/test_sql.py", _gh)
check(_r["state"] == "commented" and _gh.calls[-1][:3] == ["issue", "comment", "4"]
      and not any(c[:2] == ["issue", "create"] for c in _gh.calls), "promote: a second repo with the same name comments, never creates")

_gh = FakeGh([_issue(4, "no-raw-sql", "CLOSED", "COMPLETED")])
_r = promotion.promote("no-raw-sql", "reporting#13", "tests/test_sql.py", _gh)
_comment = _gh.calls[-1][_gh.calls[-1].index("--body") + 1]
check(_r["state"] == "commented" and "should adopt it" in _comment
      and not any(c[:2] in (["issue", "reopen"], ["issue", "create"]) for c in _gh.calls),
      "promote: a completed close gets an adopt comment, never a reopen or a new issue")

_gh = FakeGh([], fail_on="list")
_r = promotion.promote("no-x", "a#1", "p.py", _gh)
check(_r["state"] == "unknown" and "gh failed" in _r["reason"] and len(_gh.calls) == 1,
      f"promote: a gh failure is unknown with a reason, nothing written, got {_r!r}")
check(promotion.result_line(_r).startswith("PROMOTION=unknown URL=none REASON=gh failed"), "result_line: unknown carries the reason")
_gh = FakeGh([], fail_on="create")
check(promotion.promote("no-x", "a#1", "p.py", _gh)["state"] == "unknown", "promote: a failed create is unknown, never created")
check(promotion.promote("no-x", "a-pr-1", "p.py", FakeGh([]))["state"] == "unknown"
      and promotion.promote("--", "a#1", "p.py", FakeGh([]))["state"] == "unknown",
      "promote: an unreadable repo#pr or an empty rule name is unknown, never filed")

_h.report_and_exit("test_structural_scope")
