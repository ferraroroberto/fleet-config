"""Shared fixtures for driving a delivery-check script with synthetic gh data.

The post-condition scripts read GitHub through `skills/_lib/audit_issue.py`
(`_list_open` to find the managed ledger issue, `gh` to read its comments).
Patching *that* module is the one seam that is stable across fleet-config#627's
refactor: it is how both the pre-refactor `audit-fleet` script and the
post-refactor shared helper reach GitHub, so the very same driver characterizes
both and any difference in exit code is a real behaviour change rather than an
artefact of how the test reached in.

`gh` itself is never invoked. A fake `gh` on PATH would not work here anyway:
`audit_issue._run` shells out as `["gh", *args]`, and on Windows CreateProcess
searches PATH for `gh` and `gh.exe` only — never `gh.bat`/`gh.cmd`.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional


def load_module(path: Path, name: str) -> ModuleType:
    """Import a script by path without putting its directory on sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def comment(hours_ago: float, body: str = "digest", *, now: Optional[dt.datetime] = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    created = now - dt.timedelta(hours=hours_ago)
    return {"createdAt": created.isoformat().replace("+00:00", "Z"), "body": body}


class FakeGitHub:
    """Stand-in for `audit_issue`'s two read paths.

    ``issues`` is what `_list_open` returns; ``comments`` is what a
    ``issue view --json comments`` read returns. Either may be replaced by a
    callable that raises, to model an unreadable ledger.
    """

    def __init__(self, issues: list[dict], comments: list[dict]) -> None:
        self.issues = issues
        self.comments = comments
        self.raise_on_list: Optional[BaseException] = None
        self.raise_on_view: Optional[BaseException] = None

    def list_open(self, repo: str) -> list[dict]:
        if self.raise_on_list is not None:
            raise self.raise_on_list
        return self.issues

    def gh(self, args: list[str], **kwargs) -> str:
        if self.raise_on_view is not None:
            raise self.raise_on_view
        if "view" in args and "comments" in " ".join(args):
            return json.dumps({"comments": self.comments})
        return ""

    def install(self, audit_issue: ModuleType) -> None:
        audit_issue._list_open = self.list_open  # type: ignore[assignment]
        audit_issue.gh = self.gh  # type: ignore[assignment]


def ledger_issue(number: int, title: str, kind: str) -> dict:
    """An issue shaped so `audit_issue.plan` adopts it for ``kind``."""
    return {
        "number": number,
        "title": title,
        "body": f"<!-- audit-managed: kind={kind} -->\nledger body\n",
    }


def run_main(module: ModuleType, argv: list[str]) -> int:
    """Call a check script's ``main`` and normalise however it reports its code."""
    try:
        result = module.main(argv)
    except SystemExit as exc:  # a script that raises SystemExit(main())
        code = exc.code
        return 0 if code is None else int(code)
    return int(result or 0)


def characterize(
    module: ModuleType,
    audit_issue: ModuleType,
    scenarios: dict[str, Callable[[FakeGitHub], None]],
    *,
    issues: list[dict],
    argv: Optional[list[str]] = None,
) -> dict[str, int]:
    """Run ``module.main`` once per scenario; return {scenario: exit code}."""
    observed: dict[str, int] = {}
    original_list, original_gh = audit_issue._list_open, audit_issue.gh
    try:
        for label, configure in scenarios.items():
            fake = FakeGitHub(issues=issues, comments=[])
            configure(fake)
            fake.install(audit_issue)
            observed[label] = run_main(module, list(argv or []))
    finally:
        audit_issue._list_open = original_list  # type: ignore[assignment]
        audit_issue.gh = original_gh  # type: ignore[assignment]
    return observed
