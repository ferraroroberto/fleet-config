# Merge and land — linked worktree (`/issue-finish` step 5)

Loaded on demand from `/issue-finish` step 5 when pre-flight's `worktree_claim.py mode` printed `worktree`. Do both parts in order, then return to step 5's remaining bullets (issue closed, owner check, Board marker, branch residue).

- **Merge — the flag depends on the checkout mode** (from pre-flight):
  - **Linked worktree:** `gh pr merge <PR> --squash` — **no `--delete-branch`**.
    From inside a worktree that flag fails its *local* half every time
    (`'main' is already used by worktree at <primary>`): `gh` tries to check out
    the default branch to delete the ref, and the primary holds it. The remote
    merge still succeeds, so the failure is cosmetic. Delete the refs
    explicitly instead, after the landing step below:
    ```
    git push origin --delete <branch>
    git -C <repo> branch -D <branch>
    ```
    **Gate the local delete on a landed check** — confirm the tip's content is
    already in the default branch: `git -C <repo> diff --quiet
    origin/<default> <branch>` is clean. Not `git branch --merged`: the
    squash merge rewrites the SHA, so the tip is never an ancestor of the
    default branch and a genuinely-merged branch reads as unmerged
    (fleet-config#567). The same rewrite is why it's `-D`, not `-d`. The landed
    check is what makes `-D` safe — never skip it, never `-D` a tip you haven't
    confirmed landed.
- **Land + clean up, by checkout mode** (from pre-flight):
  - **Linked worktree:** do **not** `git checkout <main>` — the primary checkout
    may belong to another live session. That does **not** mean leaving it
    behind: `cd` out to the primary repo path (`<repo>`), remove this worktree,
    then **land the primary** (fleet-config#647).
    1. Remove the worktree (the helper strips the `.venv` junction *before*
       `git worktree remove`, so the primary's real venv is never touched):
       ```
       E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py remove-worktree <repo>-wt-<N>
       ```
    2. **Land the primary, or say plainly that you didn't.** A merged PR is not
       a deployed fix: until the primary tree fast-forwards, the merged change
       is not live for anyone working there — and in `fleet-config`
       specifically, `hooks/` and `skills/` reach `~/.claude` through junctions
       rooted at the primary, so the change does nothing *fleet-wide*:
       ```
       E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py land-primary <repo> <N>
       ```
       It applies the same guard as `assert-owner` (clean tree, claim free or
       owned by `<N>`) plus "already on the default branch", then a
       fast-forward guarded by `untrack_guard.py` and a `rev-list --count
       HEAD..origin/<default>` check. It prints exactly one `PRIMARY=` line
       either way, plus a `RESTORED_UNTRACKED=`/`KEPT_ASIDE=` line for each
       live file the merge untracked and the guard kept (fleet-config#1086) —
       carry those into the summary too:
       - `PRIMARY=live behind=0` — the merge is live locally.
       - `PRIMARY=stale reason=<why>` — it could not establish that. **Do not
         improvise, do not stash, do not force, do not `git checkout`.**
         Reporting stale *is* the correct outcome; recovering a dirty or
         claimed primary is never this lane's job. `reason=live process
         serving this tree (…)` is correct by design (fleet-config#665): a repo
         declaring `tray_cmd` serves out of its checkout, so fast-forwarding
         would serve one UI out of two commits. The named restart is the
         remedy — nothing here is broken.
       **Put that line in the finish summary, verbatim, next to the merge
       result** — never absent, never implied by silence. "Merged" and "live"
       are two facts and the summary carries both.
    3. **`fleet-config` only — prove the junction is serving the merge.** When
       the diff touched `hooks/` or `skills/`, landing the primary is what
       *deploys*. Read a changed file back through its `~/.claude/...` path
       (e.g. `C:/Users/rober/.claude/skills/issue-finish/SKILL.md`) and confirm
       it carries the edit — an artefact check, never inferred from the
       junction's existence. Same class as the `#199`/`#459` deploy-coverage
       gate.

    A worktree session holds no primary claim, so there is nothing to release.
