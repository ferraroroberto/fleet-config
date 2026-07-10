# Monorepo vs polyrepo: why the fleet is many repos

A learning document, not a log: the criteria behind the fleet's repo topology,
what the industry actually does, and the triggers that would reopen the
decision. Written after the July 2026 vendored-component cascade
(project-scaffolding#144–#150: one tray helper, four defect waves, ~40
mechanical re-vendor PRs across 6–7 repos in 48 hours) forced the question:
*is paying N PRs per shared change a sign we should be a monorepo?*

Short answer: no — but only because the propagation cost is being made cheap
deliberately (see "The seams"). The cascade was evidence of missing release
engineering, not of the wrong topology.

## History: the incubator pattern

The fleet *started* as a monorepo — `automation` — and it still exists,
holding small utility-script families (audio, excel, google, image, notion,
system, …). Every time something grew real weight — its own dependencies, its
own server/tray, its own deploy story — it was extracted into its own repo
with its own history and issue tracker (voice-transcriber, photo-ocr,
home-automation, …).

This is a recognized industry shape, not an accident: **monorepo as
incubator**. Cheap experiments share one roof; graduation is earned. Keep it.

**Graduation criteria** (a subtree leaves `automation` when any holds):

- It needs its own dependency set / `.venv` (its pins would fight siblings').
- It runs as a long-lived process (server, tray, scheduled job) with its own
  restart story.
- Its issues deserve their own tracker — planning noise would drown siblings.
- Its visibility differs (would be public while `automation` stays private,
  or vice versa).

## What the industry actually does

Both models are mainstream at the highest level of engineering practice.
Google, Meta and Twitter run monorepos with the **one-version rule** (every
consumer builds against HEAD of every dependency; a shared-component fix and
all its call-site updates land in one atomic commit). Amazon and Netflix run
polyrepo (service-per-repo) with heavy investment in dependency automation.
Neither camp regards the other as wrong.

The converged insight: **both models pay the same total complexity, in
different places.**

| | Monorepo pays at… | Polyrepo pays at… |
| --- | --- | --- |
| Shared change | nothing — one atomic commit | propagation: N PRs, version skew, drift |
| App-local change | isolation: entangled history, one issue tracker for everything, blast-radius tooling (build/test only what changed) | nothing — the repo *is* the isolation |
| Access control | per-directory ACLs (needs platform support) | free — per-repo visibility |
| Tooling bill | grows with scale (Bazel-class build systems, custom VCS at Google/Meta scale) | grows with sharing (manifests, sync bots, drift checks) |

So the real criterion is not taste but **where most changes land**:

- Mostly cross-cutting changes (shared libraries touched by everything) →
  monorepo, because you'd pay the propagation toll daily.
- Mostly app-local changes, episodic shared waves → polyrepo, because you'd
  pay the entanglement toll daily for isolation you use every hour.

## Why polyrepo wins for this fleet

Measured against this fleet, the criteria all point the same way:

1. **Most changes are app-local.** A normal day is issues and PRs inside one
   app. Shared-component waves are episodic (a few per month); July's cascade
   was an anomaly amplified by shipping an unbaked component, not the norm.
2. **Per-repo history and issues are load-bearing here.** The whole agent
   workflow — one issue → one branch → one PR, `/audit-fleet` per repo,
   `.fleet.toml` cards, per-repo CLAUDE.md context budgets, worktree claiming
   — assumes repo = project. In a monorepo every one of those tools would
   need path-scoping rebuilt on top (labels, CODEOWNERS, sparse checkouts),
   i.e. re-implementing the isolation repos give for free.
3. **Mixed visibility is a hard blocker.** The fleet mixes public and private
   repos; GitHub has no per-directory visibility, so a full monorepo would
   force everything private (or leak everything public).
4. **The often-cited monorepo scale problems don't apply** (one dev, tiny
   repos — no Bazel needed), but neither do its benefits: atomic cross-repo
   commits matter most when *many people* race on shared code. Solo, a
   batched propagation wave achieves the same end state a day later.

And the user-experienced costs of the old monorepo were real and are the
textbook ones: mixed history ("every change entangled with other things"),
one undifferentiated issue tracker, no per-project identity.

## The seams: what polyrepo must then pay for deliberately

Choosing polyrepo means choosing to industrialize the propagation seam
instead of pretending it's free. The July cascade happened in the gap where
that machinery was missing. The fleet's version of the standard kit:

- **Quality gate at the source** — a shared component's fix wave doesn't
  leave the scaffold until the scaffold's own behavioral tests pass; a second
  same-day bug in the same component freezes propagation until it soaks
  (project-scaffolding#152). Propagation is a multiplier: it distributes
  whatever quality you ship, including defects.
- **Version manifest + one-command fan-out** — each adopter records
  component → scaffold SHA; re-vendoring is a single command that opens
  auto-merging, issue-less PRs, Dependabot-style (fleet-config#338). "Who is
  behind" becomes a query, not an audit.
- **Right channel per component class** (the "does it ship?" rule,
  project-scaffolding#153):
  - *Ships with the app* (UI components, `_vendored/*`) → vendored copies +
    manifest + automated fan-out. Legitimate skew, visible skew.
  - *Machine-local infrastructure* (tray lifecycle) → one shared junctioned
    copy called by path; zero propagation. Vendoring bought pinning nobody
    used at 6–7× the labor.
  - *Spec / convention* (design.md, CLAUDE.md rules) → single-home-by-
    altitude, already solved.
- **Batched waves, never per-commit reflex** — collect scaffold changes,
  propagate once. Four waves in a day is the anti-pattern regardless of
  tooling.

This is Amazon's polyrepo posture in miniature: keep the repos, spend the
engineering on the seams.

## Revisit triggers

Reopen this decision (new issue linking here) if any of these hold:

- Propagation waves become weekly rather than episodic *despite* #338's
  automation — i.e. the change mix has genuinely shifted cross-cutting.
- Shared-component count grows past the point where the manifest/fan-out kit
  is itself a maintenance burden (rough line: more scaffold-fix PRs than
  app-feature PRs in a normal month).
- GitHub ships per-directory visibility/ACLs, removing the public/private
  blocker (unlikely; noted for honesty).

Even then, the likely move is a *partial* consolidation (e.g. merging the
web apps that share the most surface), not a return to one repo.

## Decision log

- **2026-07-10** — Reflection prompted by the #144–#150 cascade. Decision:
  stay polyrepo; treat the cascade as a release-engineering gap, not a
  topology error. Filed the seam kit: project-scaffolding#152 (source gate +
  freeze rule), fleet-config#338 (manifest + `/propagate-vendored`),
  project-scaffolding#153 (de-vendor machine-local tray helper). Keep
  `automation` as the incubator; graduation criteria recorded above.
