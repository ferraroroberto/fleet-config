# Watching CI (`/issue-finish` step 5, no skip-the-wait rule applied)

Loaded on demand from `/issue-finish` step 5 when neither skip-the-wait rule cleared the merge, so the remote CI run is watched. Then return to step 5's guardrails and merge.

- **Otherwise watch — but proactively, not passively.** Run `gh pr checks <PR>
  --watch`. The moment elapsed crosses the block's **investigate threshold**,
  stop waiting passively: inspect the run (`gh run view <run-id> --job <job>`)
  and classify **flake vs real failure**.
  - **Real failure** (test assertion, compile/lint/type error, a leg that isn't
    the documented flaky one) → stop and report. **Never rerun a real failure.**
  - **Documented flaky leg wedged** (per the block — e.g. the Playwright
    WebKit/PTY-input leg) → cancel + rerun **once** automatically, saying so
    (`cancelled wedged <leg> run, rerunning once`). If it flakes a **second**
    time → stop and surface it to the user; do not rerun again.
