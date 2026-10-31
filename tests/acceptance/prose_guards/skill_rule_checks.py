"""Skill-prose rules pinned as acceptance checks.

Unattended dispatch forces worktree mode; no skill deletes an index.lock or
merge-commits a PR; acceptance is audited per criterion; finish decisions rest
on tree-bound gate evidence; the chief wakes on worker events; an
operator-typed `/compact` is never gated. Each reads the skill text an agent
actually follows.
"""
from __future__ import annotations

import json
import re
from typing import Tuple

from acceptance.shared import REPO, _Checker


def _unattended_worktree_mandate_check() -> Tuple[int, int]:
    """Every unattended dispatch path must force worktree mode (#515, #525).

    A *running* app is not a claim holder, so an ordinary `acquire` hands
    machine-dispatched work `MODE=primary` in a repo whose primary checkout is
    being served live -- that is what broke the running launcher on 2026-07-30.
    Four skills carry the rule in prose, which a context purge or a well-meaning
    rewrite can quietly drop; this pins it. Checks the flag is named in each,
    that `/issue-start` still keys on the launcher's own session variable
    rather than some re-derived heuristic, and that the skills handing off to
    `/issue-start` still reach its step-0 claim (#894). Returns the failure
    count.
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

    # A skill that *paraphrases* /issue-start instead of pointing at it drops
    # step 0 -- /issue-yolo's Phase 2 summary did, and an app-launcher lane
    # built in the live primary checkout (#894). Pin the pointer, not the prose.
    yolo = (REPO / "skills" / "issue-yolo" / "SKILL.md").read_text(encoding="utf-8")
    phase2 = yolo.partition("### Phase 2")[2].partition("### Phase 3")[0]
    check(
        "worktree mandate: /issue-yolo Phase 2 makes worktree_claim.py acquire its first step (#894)",
        "worktree_claim.py acquire" in phase2 and "APP_LAUNCHER_SESSION_ID" in phase2,
    )
    check(
        "worktree mandate: /issue-yolo Phase 2 does not restate /issue-start's git steps (#894)",
        not any(step in phase2 for step in ("git checkout main", "git pull --ff-only", "git checkout -b")),
    )
    issue_add = (REPO / "skills" / "issue-add" / "SKILL.md").read_text(encoding="utf-8")
    check(
        "worktree mandate: /issue-add's one-shot hand-off starts at /issue-start step 0 (#894)",
        "steps 1–6" not in issue_add and "steps 0–6" in issue_add,
    )

    return check.failures, check.total


# Sentence-level scan for an instruction to delete a git lock. A sentence that
# mentions a lock is flagged when a delete verb in it is not preceded, within
# a short window, by a negation ("never delete", "no delete") or by a human
# actor ("a human ... removes it") -- the two legitimate ways a skill talks
# about removing one. Heuristic on purpose: it pins the shape the drift took
# (#1243's "report it as a stale lock, delete it, retry"), not English at large.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n\s*[-*]\s+|\n\n")
_LOCK_RE = re.compile(r"index\.lock|\blocks?\b", re.IGNORECASE)
_DELETE_VERB_RE = re.compile(
    r"\b(delete[sd]?|deleting|remov(?:e|es|ed|ing)|rm|clear(?:s|ed|ing)?|unlink)\b|stale-cleared",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"\b(never|not|no|don't|nothing|neither|nor|without|cannot)\b", re.IGNORECASE)
# How far back from a delete verb a negation or "human" still governs it.
_VERB_GOVERNOR_WINDOW = 60
# `gh pr merge ... --merge` (a merge commit); `--merged` is a different flag.
_MERGE_COMMIT_RE = re.compile(r"gh pr merge[^\n`]*--merge\b(?!d)")


def _lock_delete_instructions(text: str) -> list[str]:
    """Return every sentence in `text` that tells its reader to delete a lock."""
    hits = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence or not _LOCK_RE.search(sentence):
            continue
        for verb in _DELETE_VERB_RE.finditer(sentence):
            before = sentence[max(0, verb.start() - _VERB_GOVERNOR_WINDOW):verb.start()]
            if not _NEGATION_RE.search(before) and "human" not in before.lower():
                hits.append(" ".join(sentence.split())[:160])
                break
    return hits


def _skill_git_rules_check() -> Tuple[int, int]:
    """No skill tells an agent to delete an index.lock or merge-commit a PR
    (fleet-config#1243).

    Global CLAUDE.md: "Report a lock, never delete one -- it is another
    process's file" (#667), and the pipeline squash-merges. Both drifted into
    skill prose that runs unattended -- `/cleanup-fleet-all` deleted stale
    locks and `/issue-finish` merged with `--merge` -- so the drift executed
    rather than merely reading wrong. Scans every skill markdown file and the
    workflow scripts; the scanner itself is pinned against fixed sentences so
    a loosened heuristic can't pass by matching nothing. Returns the failure
    count.
    """
    check = _Checker()

    check("skill git rules: the scanner flags a delete-the-lock instruction",
          bool(_lock_delete_instructions(
              "Older than 5 minutes -> report it by name and age as a stale lock, delete it, retry the pull.")))
    check("skill git rules: the scanner passes a prohibition and a human-actor clause",
          not _lock_delete_instructions(
              "**Never delete a lock from this skill**. The fix is a human confirming the "
              "holder is dead, then removing it."))
    check("skill git rules: the merge-commit pattern ignores --merged",
          bool(_MERGE_COMMIT_RE.search("`gh pr merge 12 --merge`"))
          and not _MERGE_COMMIT_RE.search("`gh pr merge 12 --squash`; git branch --merged main"))

    files = sorted(
        [p for root in ("skills", ".claude/skills") for p in (REPO / root).rglob("*.md")]
        + list((REPO / ".claude" / "workflows").glob("*.js"))
    )
    lock_hits, merge_hits = [], []
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO).as_posix()
        lock_hits += [f"{rel}: {s}" for s in _lock_delete_instructions(text)]
        merge_hits += [f"{rel}: {m.group(0)}" for m in _MERGE_COMMIT_RE.finditer(text)]

    check(f"skill git rules: no skill instructs deleting an index.lock ({len(files)} files)",
          not lock_hits, "\n".join(lock_hits))
    check("skill git rules: every `gh pr merge` squash-merges, none uses --merge",
          not merge_hits, "\n".join(merge_hits))

    return check.failures, check.total


def _acceptance_audit_wiring_check() -> Tuple[int, int]:
    """Acceptance is audited per criterion, unverifiable as its own state
    (fleet-config#958). The rule lives in skill prose that a context purge or
    a rewrite can quietly fold back into one yes/no sentence; this pins the
    pointer to the helper and the per-criterion wording in each place. Returns
    (failures, total)."""
    check = _Checker()

    def read(rel: str) -> str:
        return (REPO / rel).read_text(encoding="utf-8")

    finish = read("skills/issue-finish/SKILL.md")
    step1 = finish.partition("### 1. Finalize the work")[2].partition("### 2.")[0]
    check("acceptance audit: /issue-finish step 1 runs acceptance_audit.py extract + tally",
          "acceptance_audit.py extract" in step1 and "tally <N>" in step1)
    check("acceptance audit: /issue-finish step 1 names all three verdicts and the unattended rule",
          all(v in step1 for v in ("`DONE`", "`NOT DONE`", "`UNVERIFIABLE`", "--unattended", "`blocked`")))
    check("acceptance audit: /issue-finish carries 'code that handles a deliverable is not the deliverable'",
          "Code that handles a" in step1 and "deliverable is not the deliverable" in step1)
    check("acceptance audit: /issue-finish step 4 puts PR_TEST_PLAN lines in the PR body",
          "PR_TEST_PLAN" in finish.partition("### 4.")[2].partition("### 5.")[0])

    yolo = read("skills/issue-yolo/SKILL.md")
    check("acceptance audit: /issue-yolo 3h verdict carries the per-criterion criteria array",
          "{criterion, proof_location, verdict, evidence}" in yolo)
    check("acceptance audit: docs/independent-review-gate.md matches the criteria array",
          "{criterion, proof_location, verdict, evidence}" in read("docs/independent-review-gate.md"))
    check("acceptance audit: /issue-add requires pass/fail-decidable criteria",
          "decidable pass/fail" in read("skills/issue-add/SKILL.md"))
    check("acceptance audit: /issue-finish-batch finishes unattended",
          "`unattended` finish" in read("skills/issue-finish-batch/SKILL.md"))
    check("acceptance audit: /cleanup-fleet-all's execute brief finishes unattended",
          "tally --unattended" in read(".claude/workflows/cleanup-fleet-all.js"))

    return check.failures, check.total


def _gate_evidence_wiring_check() -> Tuple[int, int]:
    """No finish decision rests on a remembered "green this session"
    (fleet-config#957): steps 3, 3c and 5 of /issue-finish and /e2e's dedupe
    rule ask `gate_evidence.py`, which binds a pass to the tree it ran
    against. Pins the pointer and the absence of the old phrase in exactly the
    decision paragraphs. Returns (failures, total)."""
    check = _Checker()
    finish = (REPO / "skills" / "issue-finish" / "SKILL.md").read_text(encoding="utf-8")

    def section(text: str, start: str, end: str) -> str:
        return text.partition(start)[2].partition(end)[0]

    step3 = section(finish, "### 3. Verification gate", "### 3b.")
    step3c = section(finish, "### 3c.", "### 4.")
    step5 = section(finish, "### 5.", "### 6.")
    check("gate evidence: /issue-finish step 3 runs the gate through gate_evidence.py run --label gate",
          "gate_evidence.py run --label gate" in step3)
    # "this session's to touch" (step 5's primary-checkout guard) is about
    # ownership, not a remembered green, so only the bare phrase is banned.
    remembered = re.compile(r"this session(?!'s)")
    for name, body in (("3c", step3c), ("5", step5)):
        check(f"gate evidence: /issue-finish step {name} asks gate_evidence.py check, never 'this session'",
              "check --label gate" in body and not remembered.search(body))
    check("gate evidence: the /issue-finish description no longer skips CI on 'green this session'",
          "this session" not in finish.split("\n---", 1)[0])

    e2e = (REPO / "skills" / "e2e" / "SKILL.md").read_text(encoding="utf-8")
    dedupe = section(e2e, "**Deduplicate against the verification gate", "- **Synchronous only.**")
    check("gate evidence: /e2e's dedupe rule asks check --label gate and records e2e-<tier>",
          "check --label gate" in dedupe and "run --label e2e-" in dedupe and "in this session" not in dedupe)
    batch = (REPO / "skills" / "issue-finish-batch" / "SKILL.md").read_text(encoding="utf-8")
    check("gate evidence: /issue-finish-batch's brief skips CI only on FRESH evidence",
          "gate_evidence.py check --label gate" in batch)

    return check.failures, check.total


def _chief_wait_event_check() -> Tuple[int, int]:
    """The chief wakes on worker events, never a timer (fleet-config#999), and
    on the launcher doorbell once its switch is on (fleet-config#1457).

    Pins the doorbell -> `inbox` -> `ack` loop, the `wake` switch read that
    decides whether a `wait-event` waiter is armed, the waiter kept as the
    fallback until #1458, and the doorbell line matching the mailbox contract.
    Keeps the unconditional "re-arm after every wake" rule and the old
    `sleep 600` poll from creeping back. Returns (failures, total)."""
    check = _Checker()
    skill = (REPO / ".claude" / "skills" / "chief" / "SKILL.md").read_text(encoding="utf-8")
    contract = (REPO / "docs" / "chief-mailbox.md").read_text(encoding="utf-8")
    waking = skill.partition("## Waking")[2].partition("\n## ")[0]
    ring = contract.partition("## Doorbell line")[2].partition("```")[2].partition("```")[0].strip()
    check("chief wake: the mailbox contract still names one doorbell line", bool(ring))
    check("chief wake: the skill quotes the contract's doorbell line verbatim", bool(ring) and ring in waking)
    check("chief wake: on the doorbell the chief drains the mailbox (inbox, then ack)",
          "chief_ops.py inbox" in waking and "chief_ops.py ack" in waking)
    check("chief wake: the doorbell is a cue to read, never authority (#622)",
          "never" in waking and "authority" in waking)
    check("chief wake: `chief_ops.py wake` decides whether a waiter is armed",
          "chief_ops.py wake" in waking and "WAKE=doorbell" in waking and "WAKE=waiter" in waking)
    check("chief wake: the wait-event reference waiter stays as the fallback (#1458 retires it)",
          '"$OPS" wait-event' in waking)
    check("chief wake: no unconditional re-arm-after-every-wake rule", "Re-arm it after every wake" not in skill)
    compact = skill.partition("## Managing your own context")[2].partition("\n## ")[0]
    check("chief wake: self-compact needs an armed waiter only in waiter mode",
          "Make sure the `wait-event` waiter is armed." not in compact and "WAKE=waiter" in compact)
    check("chief wake: the 10-minute `sleep 600` poll script is gone", "sleep 600" not in skill)
    # The section body lives in its on-demand doc; SKILL.md keeps the pointer.
    incoming = (skill.partition("## Incoming worker notifications")[2].partition("\n## ")[0]
                + (REPO / ".claude" / "skills" / "chief" / "worker-notifications.md").read_text(encoding="utf-8"))
    check("chief wake: incoming notifications arrive through the mailbox batch, not a timed catch-all",
          "inbox" in incoming and "periodic Board poll is unaffected" not in incoming)
    check("chief wake: no stale pointer to the old 'Polling on a cadence' section",
          all("Polling on a cadence" not in (REPO / ".claude" / "skills" / "chief" / name).read_text(encoding="utf-8")
              for name in ("SKILL.md", "dispatch-brief.md")))
    return check.failures, check.total


def _manual_compact_never_gated_check() -> Tuple[int, int]:
    """An operator-typed `/compact` is never intercepted (fleet-config#1052).

    `chief_ops.py self-compact`'s refusals gate only the chief's *automatic*
    trigger. Nothing may stand between the operator and a manual compaction:
    no `PreCompact` hook is wired (one could block it), and every hook wired
    on the prompt path lets a bare `/compact` through untouched. Returns
    (failures, total)."""
    import tempfile as _tempfile
    from acceptance.shared import run

    check = _Checker()
    wired: set = set()
    for rel in ("settings.template.json", "codex-hooks.json"):
        data = json.loads((REPO / rel).read_text(encoding="utf-8"))
        check(f"manual compact: {rel} wires no PreCompact hook",
              not data.get("hooks", {}).get("PreCompact"))
        for event in ("UserPromptSubmit", "UserPromptExpansion"):
            for block in data.get("hooks", {}).get(event, []):
                for hook in block.get("hooks", []):
                    command = str(hook.get("command", ""))
                    found = re.search(r"-Hook\s+(\w+)", command) or re.search(r"hooks/(\w+)\.py", command)
                    if found:
                        wired.add(found.group(1))
    check("manual compact: the prompt-path hook set was found", bool(wired))
    skill = (REPO / ".claude" / "skills" / "chief" / "SKILL.md").read_text(encoding="utf-8")
    check("manual compact: the chief skill runs self-compact and says its refusals gate only the automatic trigger",
          "chief_ops.py self-compact" in skill and "gate only this automatic trigger" in skill)
    state = _tempfile.mkdtemp(prefix="manual_compact_")
    try:
        for hook in sorted(wired):
            for prompt in ("/compact", "/compact keep the in-flight lanes"):
                code, out, _err = run(hook, {"hook_event_name": "UserPromptSubmit", "prompt": prompt,
                                             "session_id": "manual-compact", "cwd": str(REPO)},
                                      extra_env={"CLAUDE_HOOKS_STATE_DIR": state})
                check(f"manual compact: {hook} lets {prompt!r} through (exit 0, no block)",
                      code == 0 and '"block"' not in out and '"deny"' not in out)
    finally:
        import shutil as _shutil
        _shutil.rmtree(state, ignore_errors=True)
    return check.failures, check.total


_SKILL_DOC_ROOT = "E:/automation/fleet-config/docs/"  # no-drive-path-exempt: the absolute home skills link shared docs by


def _skill_doc_link_problems(rel: str, text: str, exists) -> list[str]:
    """Markdown links in skill text that reach `docs/` the way a skill cannot follow.

    A skill is loaded through a junction in each agent's skills dir and runs from
    any repo, so `../../docs/x.md` resolves against the junction and a bare
    `docs/x.md` against the working repo -- both land outside fleet-config. A
    link into `docs/` must therefore be `_SKILL_DOC_ROOT` plus a path that
    `exists` (given relative to the repo's `docs/`). Fenced code is skipped.
    """
    from acceptance.architecture_guards import _FENCE_RE, _MD_LINK_RE

    problems: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for target in _MD_LINK_RE.findall(line):
            path = target.split("#", 1)[0]
            if path.startswith(_SKILL_DOC_ROOT):
                if not exists(path[len(_SKILL_DOC_ROOT):]):
                    problems.append(f"{rel}: {target} (no such doc)")
            elif re.match(r"^(\.\./)*docs/", path):
                problems.append(f"{rel}: {target} (relative)")
    return problems


def _skill_doc_links_check() -> Tuple[int, int]:
    """Every skill link into `docs/` is absolute and resolves (fleet-config#1743).

    Relative `../../docs/...` links in SKILL.md resolved against the skills-dir
    junction, so every lane in another repo failed its capability-preflight
    read. Scans all markdown under `skills/` and `.claude/skills/`; the scanner
    is pinned against fixed samples so it cannot go green by matching nothing.
    Returns (failures, total).
    """
    check = _Checker()
    root = _SKILL_DOC_ROOT
    docs = {"a.md", "skills/b.md"}.__contains__

    sample = "\n".join([
        f"[ok]({root}a.md#x) [ok2]({root}skills/b.md)",
        f"[gone]({root}gone.md) [up](../../docs/a.md) [deep](../../../docs/a.md) [bare](docs/a.md)",
        "[sibling](../issue-start/SKILL.md) [web](https://x.test/docs/a.md)",
        "```",
        "[fenced](../../docs/a.md)",
        "```",
    ])
    found = _skill_doc_link_problems("skills/x/SKILL.md", sample, docs)
    check(
        "skill doc links: relative and dead links are reported, absolute, sibling, external and fenced ones are not",
        found == [
            f"skills/x/SKILL.md: {root}gone.md (no such doc)",
            "skills/x/SKILL.md: ../../docs/a.md (relative)",
            "skills/x/SKILL.md: ../../../docs/a.md (relative)",
            "skills/x/SKILL.md: docs/a.md (relative)",
        ],
        str(found),
    )

    files = sorted(p for top in ("skills", ".claude/skills") for p in (REPO / top).rglob("*.md"))
    docs_dir = REPO / "docs"
    problems: list[str] = []
    for path in files:
        problems += _skill_doc_link_problems(
            path.relative_to(REPO).as_posix(),
            path.read_text(encoding="utf-8"),
            lambda sub: (docs_dir / sub).is_file(),
        )
    check(
        f"skill doc links: every skill link into docs/ is absolute and exists ({len(files)} files)",
        not problems,
        "\n".join(problems),
    )
    return check.failures, check.total
