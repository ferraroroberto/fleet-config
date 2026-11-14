"""Standalone test-file rows for the chief (`chief_ops`, `chief_plan`) suites.

Split out of `acceptance.standalone_dispatch` so that table stays clear of the structure
ratchet's near-limit band; same row shape (label, test_file, why-standalone),
appended to `_STANDALONE_UNIT_CHECKS` there.
"""
from __future__ import annotations

from typing import Tuple

_CHIEF_UNIT_CHECKS: Tuple[Tuple[str, str, str], ...] = (
    ("chief_inbox", "chief_ops/test_chief_inbox.py",
     "the chief inbox's writer -- Claude/Codex/Pi turn-end and exit through "
     "the real adapter hooks, managed vs human-started vs undetermined -- "
     "against a temp state dir (fleet-config#999)"),
    ("chief_needs_input", "chief_ops/test_chief_needs_input.py",
     "a chief-managed worker's needs-input through the mailbox -- Claude's "
     "permission_prompt and Codex's PermissionRequest each write one "
     "needs-input event and type nothing into the chief; no live chief or a "
     "failed write pings Telegram as before, unretried; the real inbox "
     "batch line the chief sees (fleet-config#1456)"),
    ("chief_job_failed", "chief_ops/test_chief_job_failed.py",
     "a failed app-launcher Job through the mailbox -- the real inbox "
     "renders a job-failed event (head line, detail line, excerpt, "
     "null exit_code/repo, not-confirmed, any trigger as-is), count and ack it "
     "like every kind, and leave v1, v2 and needs-input lines unchanged "
     "(fleet-config#1539)"),
    ("chief_ci_failed", "chief_ops/test_chief_ci_failed.py",
     "a red GitHub CI run through the mailbox -- the real inbox renders a "
     "ci-failed event (head line, detail line, excerpt, missing fields as -, "
     "sha shortened), counts and acks it like every kind, and leaves v1, v2, "
     "job-failed and needs-input lines unchanged (fleet-config#1973)"),
    ("chief_cap_raised", "chief_ops/test_chief_cap_raised.py",
     "a raised Settings worker cap through the mailbox -- the real inbox "
     "renders a cap-raised event (old/new, a non-integer bound as -), "
     "count and ack it like every kind, and leave worker lines unchanged "
     "(fleet-config#1839)"),
    ("chief_mailbox", "chief_ops/test_chief_mailbox.py",
     "the chief mailbox v2 reader -- claim into .claimed/, ack, re-offer of "
     "an unacked claim after the stale window (injected clock), the "
     "file-name pending count docs/chief-mailbox.md defines, two racing "
     "readers, and the dispatch_id row field (fleet-config#1455)"),
    ("chief_ops_board", "chief_ops/test_board_and_delivery.py",
     "the fleet chief's board reading -- repo occupancy, the alive-worker "
     "count, find_chief -- and `steer_delivery.py`, the say --verify "
     "classifier, are testable with no live launcher or `gh` call"),
    ("chief_ops_dispatch", "chief_ops/test_dispatch.py",
     "the three dispatch refusals (occupied repo, at/over worker cap, "
     "unconfirmed yolo), cmd_dispatch with briefs, the non-loopback-host "
     "guard and the board-digest formatting hold without a launcher"),
    ("chief_ops_verify", "chief_ops/test_verify_and_verdicts.py",
     "chief verify against throwaway git repos, chief-managed marking, the "
     "fail-closed worker cap and say --verify's four distinct verdicts"),
    ("chief_ops_sessions", "chief_ops/test_session_commands.py",
     "say/stop prefix resolution, the wake doorbell "
     "and context + self-compact work with the launcher stubbed"),
    ("chief_drain", "chief_ops/test_drain.py",
     "chief_ops drain -- the auto_drain switch (off, missing and unreadable "
     "do nothing), every intake exclusion, dry-run writing nothing, and "
     "dispatch filling free slots from the top of its own rows, skipping "
     "busy repos and stopping on a refusal or unconfirmed dispatch, against "
     "a fake launcher (fleet-config#1881), and the SLOTS_IDLE line and the slots-idle read behind the chief's Stop-hook hold (fleet-config#1941)"),
    ("chief_heal", "chief_ops/test_heal.py",
     "chief_ops heal -- the chief_job_self_heal switch (off, missing and "
     "unreadable write and fire nothing), the ledger with its repeated-cause "
     "flag and no job output, and the one re-run: only a literal rerun_safe "
     "job whose fix PR's merge is in the primary HEAD, once, never retried "
     "after an unconfirmed POST, against a fake launcher and a temp git "
     "checkout (fleet-config#1897)"),
    ("chief_proposals", "chief_ops/test_proposals.py",
     "chief_ops proposals -- the chief_weekly_proposals switch (off, missing "
     "and unreadable write and deliver nothing), at most four screened "
     "candidates in public repos, one kind=proposals event per week through "
     "the real inbox, and the answers ledger: a declined idea is not offered "
     "again without new evidence, against a fake launcher, fake gh reads and "
     "a temp inbox (fleet-config#1896)"),
    ("chief_sweep", "chief_ops/test_chief_sweep.py",
     "the chief's worktree sweep -- every class (finished incl. a "
     "squash-merged branch, in-use, has-work incl. a dirty tree, "
     "orphan-empty, unknown) against throwaway worktrees, --apply removing "
     "only finished and orphan-empty and reporting a folder that stays, an "
     "unreadable board degrading removable verdicts to unknown, and the "
     "idle-finished session list (fleet-config#1496)"),
    ("audit_quote", "audit/test_audit_quote.py",
     "/codebase-audit's quote check -- a finding's verbatim quote matched "
     "whitespace- and CRLF-tolerantly against the named file, MISMATCH, "
     "NO_QUOTE and UNREADABLE as distinct non-passing verdicts, ticked and "
     "carried items skipped, and the CLI exit codes -- is testable on its "
     "own, against a synthetic repo (fleet-config#960)"),
    ("dead_code_leads", "audit/test_dead_code_leads.py",
     "/codebase-audit's dead-code pre-pass -- vulture at floor 60 filtered to "
     "function/class/method/property, framework names and whitelists dropped, "
     "test-only symbols tagged, the whole-word caller search, ruff leads "
     "skipped under the ratchet, and every failure to run (missing, crash, "
     "timeout, garbage, unreadable file list) as not-confirmed -- is testable "
     "on its own against synthetic git repos (fleet-config#1527)"),
    ("chief_managed", "chief_ops/test_chief_managed.py",
     "the chief-managed session marker -- mark read back through "
     "notify_on_idle.chief_managed_state (the one reader), cross-sid "
     "isolation, and the 24h TTL prune -- is testable on its own, with "
     "no real chief-managed.json touched (fleet-config#443)"),
    ("chief_plan", "chief_plan/test_chief_plan.py",
     "the chief's plan-file writer -- v1 validation (enums, local vs "
     "qualified refs, one-line text, duplicates, unknown fields), every "
     "one-line mutation, the byte-identical no-write on a rejected "
     "mutation, a corrupt or future-version file refused rather than "
     "overwritten, the structured questions (ids, options, `plan ask`, "
     "`unwait <id>`; fleet-config#1049) and the `chief_ops.py plan` CLI "
     "legs -- is testable on its own, with no real chief-plan.json "
     "touched (fleet-config#1034)"),
    ("chief_plan_prune", "chief_plan/test_prune.py",
     "`plan prune` -- `prune_items` dropping only the listed statuses "
     "(default merged) and leaving lanes and waiting items alone, an unknown "
     "status refused, `prune_plan_file` through the locked writer (missing "
     "file, corrupt file left byte-identical) and the CLI `PRUNED=<n>` line "
     "with a repeatable `--status` -- is testable on its own, with no real "
     "chief-plan.json touched"),
    ("chief_journal", "chief_plan/test_journal.py",
     "the chief's journal -- `chief_ops.py note` appending and skipping an exact "
     "repeat for the same kind + ref, each of the six plan transitions (add, lane, "
     "set merged, set parked, ask, unwait) logging exactly one entry and a "
     "refused or unjournalled command none, an unwritable journal never failing a "
     "plan command, and the 500-entry / 14-day trim -- is testable on its own, "
     "with no real chief-journal.jsonl touched"),
    ("chief_plan_parked_kind", "chief_plan/test_parked_kind.py",
     "a parked plan row needing a kind (owner / umbrella / blocked-by), the "
     "owner kind refused without the needs-owner label or when it cannot be "
     "read, a pre-kind row still loading and editable, the question date, the "
     "newest-first owner list and the one-time migration's guesses and "
     "apply -- is testable on its own, with no real chief-plan.json and no gh "
     "call"),
    ("chief_ops_transport", "acceptance/test_chief_ops_transport.py",
     "chief_ops against a slow or dead launcher -- the distinct "
     "`UNCONFIRMED=timeout` and `UNREACHABLE=` lines and exit codes with no "
     "traceback, and `stop` resolving a timed-out POST from one board read -- "
     "is testable on its own against a loopback stand-in (fleet-config#1581)"),
    ("chief_ops_digest_channels", "acceptance/test_chief_ops_digest_channels.py",
     "the chief board digest lists a standing `telegram:*` channel session "
     "as one `channels` line rather than a worker, through the predicate "
     "`repo_occupancy` uses, and marks the caller's own card "
     "(fleet-config#1613)"),
)
