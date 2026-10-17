# Full-app mode (`/design-review <repo> full-app`)

Loaded on demand from the `/design-review` skill (`SKILL.md`, "Full-app mode"), which keeps the argument, the hard rule "Full-app mode never changes the app" and the ratchet this file cites.

The whole-app redesign review. It investigates every tab of one app,
proposes **one shared component system**, keeps **everything reachable**,
ships the result as **Step N/M issues** one PR at a time with a **phone check
after each**, and ends by **writing what it learned back into the design
system**, so every app stays on one system and none forks its own look. The
principles it applies are `design.md`'s contracts
(page-header, glance card, action-row, avatar, status chip, meter, modal,
settings group); their rationale is `docs/design-system.md` › Round 8.
Worked example: app-launcher#1432 (two rounds, steps #1433–#1439).

Run it when an app reads as crowded or dated on the phone although the
measured review and `/design-sync` are mostly green (composition, not
tokens), or when it is the app's turn in the fleet rollout. One app at a
time: each round starts from the previous round's write-back. Unlike steps
3–6, this mode **authors** its investigation; the other hard rules still bind.

**A. Load the standard and the facts.** Read `docs/design-system.md` (the
latest Round entries) and `design.md` / `design.dark.md`. The review applies
them and finds what is missing; it does not re-derive them. Run steps 1–5
(judgment included), plus `--synthetic` where the target declares it, and
treat the failing rule ids and uncatalogued findings as evidence. Read the
target's `CLAUDE.md`, `README.md`, the source of every tab, sheet and
Settings section (read-only), and its open `design-review` /
`design-drift` issues, so known findings are not re-proposed as new.

**B. The investigation issue.** File one in the target repo through
`/issue-add` (or reuse an existing one), stating it is an investigation,
not a build: no code change, no PR, no restart. Acceptance: the inventory,
the mockup page with an everything-still-here table per tab, and a
comparison and recommendation as a text-only comment.

**C. The investigation, in this order** (it drives both the mockup page and
the comment):

1. **Inventory**: every fact and action on every tab, including drill-ins,
   menus, sheets and Settings. Give where each lives today and how often it
   is needed (`Many / day` · `Daily` · `Weekly` · `Rare` · `Once` ·
   `Debugging`). Before tagging, read the app's usage log from the
   `project-scaffolding` checkout, with its own `.venv`:
   `scripts/usage_summary.py --app <slug> --days 30 --json`.
   - **Exit 0**: derive each view's and tracked control's tag from its
     `views` / `controls` count relative to `sessions` and the window, and
     state the window, the session count and the phone/desktop split
     (`devices`) in the inventory and the report header, because the split
     decides which viewport the review weighs first. A fact or action with no
     `data-track` id stays a hypothesis, marked as one. Report counts and
     ids only.
   - **Exit 2**: no usage data yet, which is not zero usage. Say "no usage
     data yet" and keep frequency a hypothesis the owner corrects.
   - **Any other failure**: report the usage data as **unknown**, never as
     zero usage, and keep the hypothesis.
   - **Fallback when there is no usage data** (exit 2 or a failure): if
     the app polls each tab's endpoint only while that tab is shown, its
     request log is usage evidence. Count those polls per endpoint per day
     to see which tab was on screen and how often, and say the tags come
     from the request log. Report counts only (fleet-config#1386).
2. **Diagnosis**: a numbered list of what makes each tab crowded or dated,
   each item tied to a contract or rubric rule id. Check at least: distinct
   treatments before the first useful row, status colour used as decoration,
   "normal" badges repeated on every row, duplicated state, an ellipsis that
   cuts the useful part at 390px, nested or tinted surfaces, permanent
   footnotes, mixed concerns on one card, and row height.
3. **Patterns borrowed**: the apps and patterns studied, and what each lends.
4. **The shared system**: the contracts applied to this app. Give the
   header's exceptions line per tab, the glance card per tab (write the
   tab's main question first), the row anatomy, the avatar badge, the status
   chip tone map as a table of this app's states, and the components to
   build once. A needed departure from `design.md` is flagged as a
   **proposed spec change** for step F, never invented for one app.
5. **One home per fact**: a table of fact, home, and elsewhere (a smaller
   form of the same component, or none).
6. **Per tab**: what is wrong today; the frames (today, proposed, each sheet
   or menu open); and the **everything is still here** table: item, today,
   proposed, lands (`First screen` · `One tap` · `Moved` · `Unchanged` ·
   `Duplicate removed` · `New`). Nothing reachable today becomes
   unreachable, and only duplicates are removed.
7. **What moves between tabs**: only the moves.
8. **Build plan**: Step N/M, **shared parts first** so later steps are
   mostly markup, then one tab per step. Each step is one issue, one branch
   and one PR, and ships alone. Give its size, files and e2e churn.
9. **Decisions for the owner**: numbered, each with a recommendation and the
   alternative. Any one can flip without changing the rest.

A large app may take two rounds. Round 1 draws 3–4 options for the
most-used tab and compares them (taps to the key tasks, new components,
build cost, e2e churn, recommendation). Round 2 extends the chosen option to
every tab, and round 1 moves to a collapsed archive on the page.

**D. The mockup page.** One self-contained local HTML page with inline CSS
and JS and no external request. It is never committed and never attached to
GitHub. Publish it as a **private** artifact where the host has one,
otherwise print its path.

- **Colours**: the spec's tokens as CSS custom properties, copied from
  `design.md` / `design.dark.md`.
- **Frames**: each screen is a **390px phone frame** (about 780px tall,
  scrolling inside), with a caption naming the state shown. A tab's frames
  form one strip that swipes on a phone and sits side by side on a desktop.
- **Theme**: light and dark with **one theme switch for every frame**, set
  by a pre-paint `data-theme` boot script from a page-local storage key,
  falling back to `prefers-color-scheme`.
- **Data**: **invented only** (names, numbers, sessions, hosts, projects),
  with stand-in brand marks. The lede says so.
- **Order**: the C outline, after the lede and a short version, with the
  kit drawn as one frame and the archive last.

Then post the **text-only record** on the investigation issue: the inventory
summary, the diagnosis, the options or the system, the one-home table, each
tab's headline, the build plan and the decisions. Include no screenshot and
no captured text.

**E. Decisions, build issues and the delivery loop.**

1. **Record the decisions.** The owner answers them, and a dated
   **decision-log comment** on the investigation issue records the answers.
   A session that cannot ask prints the decisions with their options and
   stops: no build issue is filed before they are answered.
2. **File the steps** through `/issue-add`, in order, each titled "Step k/M
   of #N" and self-contained: the current state with `file:line`, the
   target as drawn, the decisions it carries, acceptance (the gate plus the
   phone check after merge), out of scope, and one **constraints** block
   repeated on every step. That block names the contracts in play, the tone
   map, the header rule, the gate, the restart plus a force-reload of the
   installed PWA, the public-repo rule, and one step per PR.
3. **Upstream first.** A vendored component change gets its
   `project-scaffolding` issue first, and the step references it.
4. **Build one step at a time** through `/issue-start` → `/issue-finish`.
   After each merge, run the repo's restart recipe; then the owner
   force-reloads the installed PWA and checks light and dark. The next step
   starts only after "looks right", because desktop projections are not
   authoritative for the installed shell. A step that **removes a pane**
   first gives every function it holds a new home. It then checks a
   numbered table (function, new home, click path, ✓) one row at a time
   against the live pane with invented data before the pane goes. A
   retired route (a URL hash, a stored tab value) keeps working and lands on
   the new home (fleet-config#1386).
5. **Log every refinement.** When the owner refines a step, post a dated
   decision-log comment on that step's issue that names what it supersedes
   and restates the changed acceptance lines.
6. **Close the round.** Re-run `/design-review <repo>` after the last step
   (the ledger diff is the measured record), then close the umbrella with
   its steps and PRs.

**F. Write-back (mandatory closing step).** A round is not done until its
learnings reach the standard.

1. **List the learnings**: every principle that proved out, every owner
   refinement that generalises, every spec rule found wrong or missing.
2. **Route each one to the single place it belongs.** If `design.md` or
   `docs/design-system.md` already says it, or nearly, edit that line rather
   than adding a second statement.
   - The **rule** goes to `design.md` **and** `design.dark.md` in one PR: a
     frontmatter token, a contract, a Do/Don't line.
   - The **reason** goes to a new dated Round entry in
     `docs/design-system.md`, citing the umbrella as the worked example.
   - Anything **measurable** goes into the rubric through **the ratchet**
     (`SKILL.md`, "The ratchet").
   - A **component** change goes to `project-scaffolding`'s vendored copy,
     then `/propagate-vendored`.
3. **Ship it** as one fleet-config issue through `/issue-add`, then the normal
   PR. Run `design_lint all <root> --spec <old|new> --spec-dark <old|new>`
   (`/design-sync`'s helper) on the reviewed app and at least one other web
   app. It must show no new drift, or name the drift a new rule creates on
   purpose and file it on the apps it hits.
4. **Keep it portable**: no app-specific names in the rule text, and no
   person, host or device anywhere. The app appears only as the cited
   example.
5. **Nothing learned?** Say so on the umbrella ("write-back: no new
   principle; the standard held").
