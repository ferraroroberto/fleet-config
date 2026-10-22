"""Context-filter checks on the compressor itself (`compress_output`): never silently read less than the command produced."""
from __future__ import annotations

import json
import sys

from acceptance.shared import HOOKS, REPO, _Checker, run


def faithfulness_never_reads_less(check: _Checker) -> None:
    sys.path.insert(0, str(HOOKS))
    import context_filter as _cf837  # noqa: E402

    # (a) distinct adjacent lines must never merge into one [xN] row. The old
    # run key was a *template* with paths/ids/numbers masked out, so two real
    # worktree paths rendered as `[x2] <the first one>`.
    distinct = "\n".join(
        f"     {i}\t/e/automation/{name}"
        for i, name in enumerate(
            ["app-launcher-wt-885", "local-llm-hub-wt-561", "task-os-wt-184"], start=1
        )
    )
    collapsed = _cf837.compress_output("git status --short", distinct).compressed
    missing_paths = [
        name for name in ("app-launcher-wt-885", "local-llm-hub-wt-561", "task-os-wt-184")
        if name not in collapsed
    ]
    check(
        "context_filter: distinct adjacent lines are never merged into [xN] (fleet-config#837)",
        not missing_paths,
        f"lost={missing_paths} | {collapsed}",
    )
    # Collapsing *identical* lines stays lossless, so it stays. (Signal lines are
    # the ones `_dedupe_exact` leaves intact for the run collapser to count.)
    identical = _cf837.compress_output(
        "git status --short", "\n".join(["ERROR disk full"] * 40)
    ).compressed
    check(
        "context_filter: identical adjacent lines still collapse losslessly",
        "[x40] ERROR disk full" in identical,
        identical,
    )

    # (b) a content command (`cat`/`tail`) is verbatim-or-byte-truncated, never a
    # signal-line extract. `cat` of a 400-line source file used to come back as
    # the ten lines containing the word "fail"; a monitoring digest came back as
    # its five least informative rows, reading exactly like "nothing happened".
    digest = "\n".join(
        [f"job {n}: failed" for n in ("Insights", "Cleanup", "Context Audit")]
        + [f"session 01K4{c}{c} state=working repo=E:/automation/app-launcher" for c in "ABCDEFGHIJKLMNOPQRST"]
        + ["worktree E:/automation/app-launcher-wt-885",
           "worktree E:/automation/local-llm-hub-wt-561",
           "open PR #905", "open PR #918"]
    )
    cat_result = _cf837.compress_output("cat E:/tmp/monitor_tick.txt", digest)
    dropped = [
        needle for needle in ("01K4TT", "local-llm-hub-wt-561", "#905", "#918")
        if needle not in cat_result.compressed
    ]
    check(
        "context_filter: cat output is verbatim, not a signal-line extract (fleet-config#837)",
        not dropped and cat_result.compressed_line_count == cat_result.line_count,
        f"dropped={dropped} lines={cat_result.line_count}->{cat_result.compressed_line_count}",
    )

    # (c) over the char budget, the loss is announced with counts — the floor is
    # "byte-truncated raw", never a summary the reader cannot tell from empty.
    oversized = _cf837.compress_output(
        "cat big.log", "\n".join(f"line {i} payload payload payload" for i in range(2000))
    )
    check(
        "context_filter: over-budget content output is byte-truncated with a counted marker (fleet-config#837)",
        "VERBATIM HEAD" in oversized.compressed
        and "withheld past" in oversized.compressed
        and "line 0 payload" in oversized.compressed
        and oversized.compressed_line_count < oversized.line_count,
        oversized.compressed.splitlines()[-1] if oversized.compressed else "<empty>",
    )


def diffs_and_json_are_payload(check: _Checker) -> None:
    sys.path.insert(0, str(HOOKS))
    import context_filter as _cf837  # noqa: E402

    # (b2) A diff is content too (fleet-config#1303): every hunk survives, where
    # the generic head/signal/tail pick kept 33 of a 201-line diff.
    diff_text = "\n".join(f"+added line {i}" for i in range(201))
    for diff_cmd in ("git diff", "git -C /r diff HEAD~1", "git show abc123", "gh pr diff 12"):
        diff_result = _cf837.compress_output(diff_cmd, diff_text)
        check(
            f"context_filter: `{diff_cmd}` output keeps every hunk line (fleet-config#1303)",
            diff_result.compressed_line_count == 201 and "+added line 100" in diff_result.compressed,
            f"lines={diff_result.line_count}->{diff_result.compressed_line_count}",
        )
    check(
        "context_filter: a non-diff git subcommand is still summarised (fleet-config#1303)",
        not _cf837.is_content_command("git status") and not _cf837.is_content_command("gh pr view 12"),
    )

    # (c2) JSON is payload too (fleet-config#1269): a key list in place of the
    # values read as the whole result, and a single-line output never got the
    # retrieve footer because its line count could not drop.
    issue_json = json.dumps({"number": 1, "title": "t", "body": "body-sentinel " * 300})
    json_result = _cf837.compress_output("gh issue view 1 --json number,title,body", issue_json)
    check(
        "context_filter: JSON output comes back verbatim, not as its key list (fleet-config#1269)",
        json_result.compressed == issue_json and not getattr(json_result, "withheld", True),
        json_result.compressed[:200],
    )
    huge_json = json.dumps([{"id": i, "note": "x" * 40} for i in range(1000)])
    huge_result = _cf837.compress_output("gh api repos/o/r/issues", huge_json)
    check(
        "context_filter: one over-budget line is truncated with a char-counted marker (fleet-config#1269)",
        getattr(huge_result, "withheld", False)
        and huge_result.compressed.startswith(huge_json[:500])
        and f"of {len(huge_json)} chars" in huge_result.compressed,
        huge_result.compressed.splitlines()[-1] if huge_result.compressed else "<empty>",
    )


def background_command_not_wrapped(check: _Checker) -> None:
    # (d) a backgrounded command is never wrapped: the harness imposes no cap on
    # one, so the wrapper's fixed 600s ceiling *introduces* a kill. A 10-minute
    # monitoring poll came back as `exit code 124` with the tick lost.
    for background, expect_passthrough in ((True, True), (False, False)):
        bg_payload = {
            "tool_name": "Bash",
            "cwd": str(REPO),
            "tool_input": {"command": "git log --oneline -5", "run_in_background": background},
        }
        code, stdout, stderr = run(
            "context_filter_hook", bg_payload, {"FLEET_CONTEXT_FILTER_MODE": "rewrite"}
        )
        check(
            f"context_filter_hook: run_in_background={background} -> "
            f"{'passthrough' if expect_passthrough else 'still wrapped'} (fleet-config#837)",
            code == 0 and (stdout.strip() == "") == expect_passthrough,
            stdout + stderr,
        )

