# Security findings — redacted issue + immediate self-heal

Loaded on demand from the `/codebase-audit` skill (`SKILL.md`, step 8b). "Hard rules" and step numbers refer to that file.

**One repo → one branch → one PR → one redacted issue, no matter how many gaps** —
tracked by the single `audit: security findings` issue, never N public security
commits.

Do this in order; **run it inline (synchronously) in your own agent context — do
NOT spawn a nested background sub-agent for the fix.** A nested background agent
does not get an auto-resume wake-up (global CLAUDE.md, "A sub-agent does not
self-resume"), so under `/audit-fleet` it would silently stall.

1. **Claim the repo in forced worktree mode** (same collision primitive
   `/issue-start` uses) so a concurrent `/cleanup-fleet` / human session on this
   repo can't clobber you and vice-versa. `--force-worktree` skips the primary
   claim entirely: unattended fleet-wide dispatch, and a *running* app or a live
   junction is not a claim holder, so an ordinary `acquire` would hand you
   `MODE=primary` and have you edit files a live process is serving
   (fleet-config#515). Then `cd` into the printed `WORKTREE=` path — everything
   after this step happens there, never in the primary checkout:
   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py acquire <repo-root> --issue <security-issue-or-0> --force-worktree
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/worktree_claim.py setup-worktree <repo-root> <security-issue-or-0> <branch>
   ```
   A live-e2e guard refusal is a hard STOP — report it and stop; setting
   `E2E_LIVE=1` or any equivalent override is forbidden.

2. **File the redacted issue** via the helper — **no vulnerability detail, ever**:
   not the class, not the file, not the line, not a description. Title exactly
   `audit: security findings`, label `security`. Body is only:
   `A security gap was detected by /codebase-audit and is being self-healed in
   this run. Detail is deliberately omitted from this public issue; see the
   private security alert for the fix PR.` — nothing more.
   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
     --repo <OWNER/REPO> --kind security --label security \
     --title "audit: security findings" --body-file <tmpfile>
   ```

3. **Fix + prove it, on one branch.** Run the `/issue-yolo <N>` flow against that
   issue (branch off fresh `main`, patch every held-aside gap), with **two
   non-negotiable additions**:
   - **A regression test per gap is mandatory.** The fix ships with a test that
     exercises the specific gap — fails before the patch, passes after. This
     test is the coverage that makes unattended auto-merge safe: it catches a
     wrong fix, so on a repo with a thin suite the fix is never resting on a
     bare byte-compile. (Global CLAUDE.md: "Reproduce before fixing" / empirical
     proof.)
   - **Every artifact stays generic.** Commit message, PR title, PR body, the
     test name and any comment — none may name the vulnerability class (no "SQL
     injection", "XSS", "hardcoded credential", "path traversal", …). Use
     `fix: harden input handling in <module>` shapes. The public diff already
     reveals the fix on a public repo, so the mitigation is a *short exposure
     window + a private review*, not secret text — don't add a neon label on top.
   - Run the repo's **own verification gate** (per its CLAUDE.md) — the new
     regression test included — as the hard pass/fail.

4. **Auto-merge on green** (green = gate passes *including* the new test), exactly
   like `/cleanup-fleet`'s easy tier: PR, wait for CI per `/issue-yolo`'s rules,
   then merge + land per `/issue-yolo` step 8's **worktree** branch — `gh pr
   merge <PR> --merge` with **no `--delete-branch`** and no `git checkout main`
   — **tear the worktree down, land the primary, and release the claim**
   (`worktree_claim.py remove-worktree <worktree-path>`, then `land-primary
   <repo> <N>` — report its `PRIMARY=live behind=0` / `PRIMARY=stale
   reason=<why>` line, since a merged fix that never reached the primary is not
   live — then `release <repo>`; verify `CLAIM=free` and that `git worktree
   list` shows the primary only; never `rm -rf` a worktree, its `.venv`
   junction would take the primary's real venv with it). Delete the branch refs
   explicitly (`git push origin --delete <branch>`; local `-D` only after
   confirming the tip landed in `origin/<default>`). Tray restart
   follows `/issue-yolo`'s safety rule: a detach-compliant tray restarts; an
   unsafe/silent tray is **not** restarted unattended — note "tray not restarted,
   still on old build" in the alert instead.

5. **Close the redacted issue**, referencing the merged PR by number only (still
   no vuln detail in the close comment).

6. **Fire the private security alert** — the review channel the public issue
   deliberately lacks, so you can inspect the actual fix and revert if it's
   wrong. Routes to the attention chat, not the log:
   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/hooks/notify_complete.py \
     --kind security --issue <N> --pr <PR> --pr-url <PR_URL> --summary "auto-merged, review the diff"
   ```

**Escalate instead of merging blind when the safety net is absent.** If the
repo has **no test surface at all** to add a regression test to, or the
verification gate / added test does **not** pass, or `/issue-yolo`'s validation
fails for any reason: **do not merge.** Leave the branch in place, leave the
redacted issue **open**, and fire the same `--kind security` alert with
`--summary "escalated - needs manual /issue-finish"` (drop `--pr`/`--pr-url` if
no PR was opened). Never retry a failed security fix by guessing, and never
force-merge one — half-healing a gap unreviewed is worse than leaving it for
the human the alert just pinged.
