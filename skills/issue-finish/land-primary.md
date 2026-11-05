# Merge and land — primary checkout (`/issue-finish` step 5)

Loaded on demand from `/issue-finish` step 5 when pre-flight's `worktree_claim.py mode` printed `primary`. Do both parts in order, then return to step 5's remaining bullets (issue closed, owner check, Board marker, branch residue).

- **Merge — the flag depends on the checkout mode** (from pre-flight):
  - **Primary checkout:** `gh pr merge <PR> --squash --delete-branch` — squash
    merge (the fleet pipeline's rule); branch deleted on both remote and local.
- **Land + clean up, by checkout mode** (from pre-flight):
  - **Primary checkout:** before switching, guard against landing this merge on
    a tree that isn't this session's to touch — the claim system routes a
    *second* session into a worktree but never re-checks who holds the claim at
    the moment something runs `git checkout <main>` here (fleet-config#473):
    ```
    E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py assert-owner <repo> <N>
    ```
    `ASSERT_OWNER=pass` (tree clean, claim free or owned by `<N>`) → proceed.
    `ASSERT_OWNER=refuse: <reason>` (dirty tree, or another issue's claim live)
    → **stop immediately, do not checkout or pull** — surface the refusal
    reason rather than improvising a recovery. Only on a pass: `git checkout
    <main>`, then fast-forward it through the untrack guard (never a bare
    `git pull` — it deletes every live file the merge untracks,
    fleet-config#1086):
    ```
    E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/untrack_guard.py fast-forward .
    ```
    `FF=done` → carry any `RESTORED_UNTRACKED=`/`KEPT_ASIDE=` line into the
    step-7 summary; `FF=refused` → stop and report. Then release the claim so
    the next session can own the primary:
    ```
    E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py release <repo>
    ```
    **Verify the release** — a hard acceptance step. A finisher that is not the
    acquiring session (build-and-stop → separate finisher, `/cleanup-fleet`,
    `/issue-finish-batch`) or an abbreviated finish must not silently skip it
    and leak the claim until the 8h TTL (fleet-config#174). Immediately run:
    ```
    E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py status <repo>
    ```
    and confirm it prints `CLAIM=free`. Still shows `CLAIM=held` → release did
    not take — re-run `release <repo>` and re-check before reporting the finish
    done.
