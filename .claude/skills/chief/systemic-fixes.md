# Proposing systemic fixes

Loaded on demand from the `/chief` skill (`SKILL.md`, same section name).

When an incident, a lane failure, a repeated steer or a review finding shows a **class** of problem, look past the instance and propose the systemic fix, early and positively. The 2026-11-17 port exhaustion is the model: one context-per-capture browser walk became a shared paced-browser helper, a doc rule and a ratchet. Fixing the instance stays the incident rules' job (`machine-critical.md`, `job-failed.md`, `ci-failed.md`); this is the step after it. It is separate from the scheduled candidates in [proposals.md](proposals.md).

- **Trigger:** 2+ occurrences, or a mechanism that reproduces the problem by construction (nothing stops the pattern and many callers write it). One incident with neither is not a pattern.
- **Bar:** only what a senior architect accepts in review. The smallest fix at the right altitude (a scaffold helper, ratchet or convention per project-scaffolding's `docs/architecture-decisions.md`, a brief rule, a guard in a shared tool), extending an existing mechanism before adding one. No fix for a one-off, no speculative abstraction, no rule without a way to enforce or check it.
- **How:** one `plan ask` per pattern with the evidence, the proposed fix, its cost, what it prevents and your recommendation. File issues only after the owner says yes, under [filing-discipline.md](filing-discipline.md). A fix the owner already asked for is just done.
- **Memory:** `chief_ops.py note` the proposal, and the answer when it arrives. A declined pattern is not proposed again unless new evidence arrives.
