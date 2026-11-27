# Owner checks

Some work needs Roberto: after it ships, he has to try it as a user, or before it starts, he has to do a named step (a device probe, a signup, a decision). Owner checks make both states explicit. Code marks them at fixed points of the issue workflow; nobody's memory has to. The GitHub issue is the source of truth: the Board, the chief and every skill read it from there.

Self-verification comes first. Before calling anything unprovable, a lane runs every deterministic proof it has: the gate, the original repro re-run, a screenshot baseline or visual proof, rendered geometry, and an API probe. A criterion those can show is ticked with its evidence, never sent to Roberto. Owner validation covers only what no session can do, named per criterion with one owner-only class (see [Owner-only gaps](#owner-only-gaps)). A new page or view is browser-provable, so it never needs him: the lane proves it with a fresh visual proof. A criterion that only a later scheduled run can show is the chief's to read, never the owner's.

## Owner-only gaps

Before declaring any gap, a lane uses every proof tool the fleet has:

- Playwright Chromium and WebKit with phone profiles
- the Mac runner (Xcode, simulator)
- screenshots and `/design-review`
- the launcher API for throwaway sessions
- the passwordless SSH to satellites
- the repo's restart recipe and build-identity check
- the local hub's TTS for real speech input
- pywinauto/UIA for Windows windows

Visual verification by the lane is enough. What is left goes to Roberto only as `--gap <class>:<criterion>` with one of seven classes:

| Class | Only for |
| --- | --- |
| `device` | Real phone hardware or a physical sensor no emulation reaches. The criterion must name one of: on-screen keyboard, keyboard accessory row, mic or microphone, lock screen, background audio, home-screen or PWA install, haptics, camera, car, a physical home device. Anything else is refused with "prove it with emulation". |
| `owner-account` | An action in an account only Roberto holds, with real side effects or a sign-in only he has (moving real mail, a signup, a login). |
| `physical` | A physical action no automation can perform (plugging hardware, being in a room). |
| `third-party` | A person other than Roberto, or a paid or external service he must sign up for. |
| `input-device` | A physical input device no emulation reaches. The criterion must name a pen, stylus, digitizer or drawing tablet. |
| `desktop-session` | A locked or interactive desktop session, or a third-party recording app, that no headless lane can hold. The criterion must name a locked desktop or session, an interactive or signed-in desktop, or a recording app. |
| `second-machine` | A clean second computer the lane has no access to. The criterion must name a second, another, clean or fresh computer, machine, PC or laptop. A satellite reachable over SSH stays lane-provable. |

A criterion that names a pen or a locked or interactive desktop is not refused for the browser- and window-driving words (desktop, keys, click, drag, touch): Playwright and UIA cannot reach those, so they are no substitute. Every other refusal below still applies.

There is no taste class: a genuine design choice is a plan-gate question before the build, never a validation after it.

`finish` refuses (exit 2) any gap whose criterion names something lane-provable, whatever its class, and the refusal names the tool to use instead. The list is closed and matches whole words:

| Criterion names | Prove it with |
| --- | --- |
| restart, rebuild, build, version, sha | the restart recipe and its build-identity check |
| screenshot, look, layout, light, dark, mock-up | visual proof and `/design-review` |
| desktop, keyboard, key, click, Ctrl, Enter, Escape, drag | Playwright, or pywinauto/UIA for a Windows window |
| phone width, iPhone layout, touch | the WebKit phone profile |
| session, lane, launch | the launcher API |
| SSH, satellite | the passwordless SSH |
| job | the launcher Jobs API |
| CI, workflow | the local gate; CI being off is a blocked-by |
| a bare issue reference (a hash sign and a number) | a blocked-by on that issue |

An allowlisted device item is cut out before the list runs, so "on-screen keyboard" is not refused as a keyboard check. Whether a criterion truly needs an allowlisted item stays the lane's judgement; that is the narrow part a rule cannot decide.

## The two states

| Kind | Label | Means | Leaves the list |
| --- | --- | --- | --- |
| `validate-after` | `needs-validation` | Shipped; Roberto must try it as a user. The issue still closes on merge, and the label keeps it on the Board. | His answer on the Board |
| `before-start` | `needs-owner` | Cannot start until Roberto does the named step. `/issue-start` and `chief_ops.py dispatch` refuse it. | His answer on the Board |

`owner_check ensure-labels` creates both labels, with fixed colours and descriptions, in every fleet repo. `mark` and `finish` also create a missing label in their own repo before applying it.

## The comment block

Each state change posts one comment. The newest valid block on the issue wins.

```text
<!-- owner-check v1 -->
**Owner check: validate after**

- kind: validate-after
- reason: needs a real device or live input (mic), which a session cannot drive
- try: on the PC, join a real online call and watch the overlay for hints
- run: terminal: `cd /d <repo folder> && tray.bat --restart`
- run: screen: `the overlay window during a real call`
- expect: a hint appears within a few seconds of the first question
- proof: https://github.com/<owner>/<repo>/pull/<N>, <private artifact URL>
- related: #<M>, #<K>
- since: 2026-11-01
```

| Field | Rule |
| --- | --- |
| `kind` | `validate-after`, `before-start` or `cleared`. Required. |
| `reason` | Why it needs Roberto: the rule's reasons, joined with `; `. |
| `try` | What to do and why, in a sentence: where to go, what to press. |
| `run` | The exact step, one line per command, in order, as `<where>: <text>`. See [The exact step](#the-exact-step). |
| `expect` | What success looks like: what appears, or what changes. |
| `proof` | Comma-separated links: the PR first, then any private artifact or proof page. |
| `related` | Other issues of the same piece of work (back-fill only). |
| `since` | UTC date the state began. |
| `answer` | Roberto's Board answer (`cleared` blocks only). |

Every value is one line; only `run` repeats. Empty fields are left out. A block without `run` and `expect` is still read, with both empty. Repos are public, so a block never carries a screenshot, a personal path (a user profile, a data folder, a hostname) or personal content; the fleet checkout path under `E:utomation` is the one path a `run` command may use. Proof links point to private artifacts or the PR, and a private repo's block uses generic wording.

## The exact step

The owner copies, runs and answers, without opening the issue. Each `--run` is `<where>: <text>`:

| Where | Text |
| --- | --- |
| `terminal` (also `plain terminal`) | a literal command for a plain terminal |
| `elevated` (also `elevated shell`) | a literal command for an elevated shell |
| `browser` | a URL to open on the PC |
| `phone` | a URL, or the app and screen on the phone |
| `screen` | the screen or control of a desktop app or device |

A step with no command (a signup, a device check) states the exact URL or screen. A command that needs a folder carries its own `cd` line or a full path. Never a value from config or a secret: name the setting. A command that does not exist is never invented; the item stays without one and is reported.

`skills/_lib/owner_check/step.py` refuses (exit 2), before any write:

- no `--run` or no `--expect`
- a `where` outside the table, or a line break inside a `--run`
- text that sends him elsewhere ("the documented command", "see the issue", "follow the .md file", "as described"), in `--run`, `--expect` or `--try`
- a `<placeholder>` or an ellipsis in a `--run`
- a sentence where a command belongs, a `browser` step with no URL, a one-word screen
- what looks like a secret value
- an `--expect` that only says it works

## The rule

`skills/_lib/owner_check/rule.py` decides `required` or `none` and lists every reason that fired. It only adds reasons; an explicit `none` never suppresses one.

| Clause | Applies to | Reads |
| --- | --- | --- |
| The issue says `Owner validation: required` | any type | the body's `Owner validation:` line |
| A real device or live input: microphone, mic, camera, webcam, WASAPI, dictation, voice notes, iPhone, iPad, Android, on-device, haptics, on-screen keyboard, home-screen, Bluetooth; silent once the lane passes `--verified yes` or `scheduled` | any type | the title |
| A changed live-input path (`wasapi`, `mic`, `microphone`, `camera`, `webcam` path tokens), except `*.md` and anything under `docs/` or `tests/` | any type | the PR's files |
| A third party's account: sign-up, OAuth, subscription, `sign in with`, or a named provider's account or sign-in | enhancement | the title |
| Each named gap: a criterion the lane could not prove, with its owner-only class and not refused as lane-provable | any type | `finish --verified no --gap <class>:<criterion>` |

Words are matched in the title only. Issue bodies quote phone viewports, network loopback and "report" so often that body matching flagged more issues than there were enhancements. The app's own password sign-in is drivable with test credentials, so it never fires. A UI tweak a screenshot baseline proves needs nobody. A bug the lane fixed and re-verified in-session needs nobody either, unless its diff changes a live-input path. A title word is a guess made before the work exists, so recorded in-session proof outranks it.

`unknown` is its own state, never folded into `none`. The CLI produces it when the issue or the PR cannot be read, or when a required label or comment write did not land.

## Where it runs

Every `<repo>#<N>` argument (`gate`, `finish`, `mark`, `clear`) also takes the owner-qualified `<owner>/<repo>#<N>` that `gh` prints. The owner must be the fleet owner; any other owner, or any other shape, is refused with an error naming both accepted forms.

| Point | Command | Effect |
| --- | --- | --- |
| `/issue-add` step 6 | `owner_check classify --body-file <draft> --title "<title>" --label <type>` | The verdict for the template's `Owner validation:` line. The model writes the why, the "try" text and the `- run:` / `- expect:` lines under it; a `required` draft without them gets `ASK=`, and a vague `try`, `run` or `expect` is refused (exit 2). |
| `/issue-start` step 2 | `owner_check gate <repo>#<N>` | `OWNER_GATE=blocked` stops with the reason, the try text and any `RUN=` / `EXPECT=` lines. `unknown` also stops. |
| `/issue-finish` step 5, after merge | `owner_check finish <repo>#<N> --pr <PR> --verified yes\|scheduled\|no [--gap <class>:<criterion> …] --try "…" --run "…" [--run "…"] --expect "…" [--proof URL …]` | When required, it labels the issue `needs-validation` and posts the block. It writes nothing and exits 2 without `--verified`, for `no` without a valid `--gap` (a lane-provable criterion is not valid, see [Owner-only gaps](#owner-only-gaps)), for a `--gap` beside `yes`/`scheduled`, or without `--try` and a complete [exact step](#the-exact-step) on a required verdict. An enhancement that adds a page (`.html`) or a view module (`views/*.js`, `*-view.js`) outside `_vendored/`, `tests/`, `docs/` and `fixtures/` is refused too until the PR body carries `VISUAL_PROOF=fresh`: a browser proves it, never the owner. A `none` re-run for the same PR removes the `needs-validation` label an earlier `finish` for that PR added and posts a `cleared` block (`RETRACTED=yes`); a hand mark or another PR's block is left alone. |
| Chief, before dispatch | `chief_ops.py dispatch` | Refuses a `needs-owner` issue. An unreadable issue only warns, because `/issue-start`'s gate is the hard stop. |
| Chief, reading | `chief_ops.py owner-checks` | Every open owner item fleet-wide, from the labels. |
| Chief, parking | `owner_check mark <repo>#<N> --kind before-start --reason "…" --try "…" --run "…" --expect "…"` | Owner-blocked work gets the label first; `plan set --status parked --kind owner` then records it and is refused without the label. The `--try` text must name the exact owner action, where to do it and which answer unblocks the work, so the Board question answers cold; an empty, placeholder or under-six-word action is refused (exit 2). A block that is really an agent's decision goes to the chief, never the owner. |
| Adding the step to an open note | `owner_check amend <repo>#<N> --run "…" --expect "…" [--try "…"]` | Posts the note again with the step; `reason`, `proof`, `related` and `since` stay, so the item keeps its place. Refused when the issue has no open note. |
| Any write of a label | `github.mark`, behind `finish` and `mark` | The block is posted first and the label applied second, so a failure between the two never leaves a label with no note. `mark` refuses (exit 2) an empty `--reason` or `--try`, or no [exact step](#the-exact-step). A label put on by hand has no write path to guard; `owner_check repair` finds it (see [Repairing a label with no note](#repairing-a-label-with-no-note)). |
| Board answer | `owner_check clear <repo>#<N> --answer "…"` | Removes the label and posts a `cleared` block with the answer. Every other issue in the same repo labelled for the same kind whose block carries the identical `try`, `run` and `expect` is cleared with it (`MERGED_CLEARED with=… also=…`), since `list` showed them as one line. |

`--verified yes` means the original repro or acceptance re-ran and passed in this session. Pass `scheduled` when everything did except a criterion that only a later scheduled run can show: it adds no reason and prints `SCHEDULED_READ=chief`, the chief reading that run. Pass `no` only for a criterion the deterministic proofs cannot show, with one `--gap <class>:<criterion>` per such criterion; each becomes its own reason. A generic "could not verify" names nothing Roberto can act on, so `no` without a gap, and a missing `--verified`, are refused.

Run every command as `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/owner_check/cli.py <command> …`.

## Exit codes and output

| Command | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| `classify`, `finish` | none | required (`APPLIED=yes` or `already`) | refused (`finish`): no `--verified`, `no` without a valid `--gap`, an unproven new page or view, or required with no `--try` | unknown |
| `gate` | clear | blocked | | unknown |
| `mark`, `amend`, `clear`, `ensure-labels`, `backfill`, `reclassify`, `repair`, `list` | every write or read landed | | refused (`mark`, `amend`): a vague or missing field, or (`amend`) no open note | something did not land (`list`: a repo unread, or a repair GitHub did not answer), or (`repair`) a note could not be built; named on its own line |

Each command prints `KEY=value` lines (`OWNER_CHECK=`, `OWNER_GATE=`, `REASON=`, `TRY=`, `RUN=`, `EXPECT=`, `ASK=`, `MARKED=`, `AMENDED=`, `CLEARED=`, `UNKNOWN=`).

## Readers

`owner_check.list_items(repos=None)` returns `{"items": [...], "unknown": [{"repo", "reason"}]}`, one item per labelled issue (open or closed). Each item has `repo`, `number`, `ref`, `title`, `url`, `state`, `kind` and `block`, plus the block's `reason`, `try`, `run` (a list of `{"where", "text"}`), `expect`, `proof` and `since`, and a `note` state:

| `note` | The item |
| --- | --- |
| `complete` | has `run` and `expect`: show the `run` lines to copy, and `expect` under them |
| `no-run` | has a readable note without the exact step |
| `missing` | has no note, and none was tried (`--no-repair`, or `list_items` itself) |
| `unrepairable` | has no note and `list` could not build one; `note_problem` says why |
| `unknown` | has no note and the repair did not get an answer from GitHub; `note_problem` says why |

A labelled issue with no matching block is still an item, with `block: false` and empty fields; a reader never invents them. A repo that could not be read lands in `unknown`, never in an empty list. A repo with GitHub Issues switched off holds no items and is not `unknown`. `owner_check list --json` prints the same shape, with one line per flow: items of one repo and kind whose `try`, `run` and `expect` are identical merge into the first one, which lists the others' refs under `merged`. The match is exact text, never fuzzy, and an empty `try` never merges. `owner_check list` first writes every missing note it can ([repair](#repairing-a-label-with-no-note)), so a gap heals the next time the Board reads; `--no-repair` reads only. `list_items` itself does not merge. Items come newest `since` first, undated last, so the Board's "Waiting for start" and `chief_ops.py owner-checks` both put the most recent step on top and show its date.

GitHub's issue-list index trails a label write by a few seconds, so an item marked moments ago can be missing from `list` while the issue itself already carries the label. Read the issue (`gate`) when it has to be current to the second.

## Re-classifying the list

A recorded block joins its reasons with `; `, and a gap's criterion may hold one itself, so a gap is read as one reason from its opening words to its closing `because it needs <class>` clause.

`owner_check reclassify [--repo R] [--apply]` re-runs the rule over every `needs-validation` issue and clears the label where no reason fires any more, with a `cleared` block that names the rule that stopped applying. A dry run prints `RECLASS keep|would-clear|unknown <ref>` per item and a `RECLASSIFY=` count line, and writes nothing. Three kinds of recorded reason are never recomputed, so they keep the item: a lane's named gap (a criterion with an owner-only class), unless the narrowed classes now refuse it as lane-provable or its class (`taste`) is gone, any text a person wrote by hand, and any item whose PR cannot be read (`unknown`, never cleared). A generic "the lane could not verify" or "did not state" reason names no criterion and no class, so it drops. No in-session proof is inferred from history, so a recorded device reason still keeps its item. Everything else is recomputed from the issue title and body and the PR's files; a recorded new-page-or-view reason drops, since a browser proves it. A back-fill-only entry has no rule reason, so it clears.

## Repairing a label with no note

A label with no block of its own kind shows on the Board as "Details unknown": the owner cannot tell what to try. It arrives by hand, from an older flow, or after a block that was cleared and the label re-added.

`owner_check list` runs this repair itself, over the items it has just read. `owner_check repair [--repo R] [--apply]` lists every labelled issue whose latest block is missing or of another kind (open or closed, as `list` reads them) and, with `--apply`, posts the missing block. It never adds, removes or changes a label, never closes an issue and never writes a `run` or `expect`: a command nobody recorded is not invented, so a repaired item is `no-run` until `amend`. The note's `reason` is "Labelled for an owner check with no note on record"; its `try` is built from the first source that has unticked `- [ ]` items: the issue body, then each closing PR's body (up to three, one line each). With none, a validate-after note falls back to the generic "use what shipped once" line, and a before-start item gets no note, because an owner step must name a concrete action (`vague_action`). That item prints `REPAIR unbuildable <ref>`, the run exits 3, and the label stays until someone writes the step with `mark`. A dry run prints `REPAIR planned|unbuildable|unknown <ref>` per item and a `REPAIR=` count line, and writes nothing.

## Back-fill

`owner_check backfill --list <file> --since <date> [--until <date>] [--apply]` merges two candidate sources:

- **An audit list.** Each `|`- or `;`-separated segment is one item: `repo#N`, ranges (`#10-12`) and `,#M` continuations, with the short names `fs`, `cm`, `al`, `hub`, `lite` and `grocery`.
- **The rule's dry run.** It covers issues closed as completed in the window, using the title clauses only, since history has neither a diff nor a verification statement.

An item spanning several issues is one Board entry. Its lowest-numbered issue carries the label and the block, with leading PR numbers skipped; the rest are listed in `related`. An issue that already has an owner-check label or block is skipped. The block's reason and try text are generic, so a private repo's content never reaches a comment. Without `--apply` it prints the plan and writes nothing. Its notes carry no `run` or `expect`, so they are `no-run` until `amend`.
