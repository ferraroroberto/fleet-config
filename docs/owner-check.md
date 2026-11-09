# Owner checks

Some work needs Roberto: after it ships, he has to try it as a user, or before it starts, he has to do a named step (a device probe, a signup, a decision). Owner checks make both states explicit. Code marks them at fixed points of the issue workflow; nobody's memory has to. The GitHub issue is the source of truth: the Board, the chief and every skill read it from there.

Self-verification comes first. Before calling anything unprovable, a lane runs every deterministic proof it has: the gate, the original repro re-run, a screenshot baseline or visual proof, rendered geometry, and an API probe. A criterion those can show is ticked with its evidence, never sent to Roberto. Owner validation covers only what a session truly cannot prove, named per criterion with one owner-only class: `device` (a real device or live input: mic, camera, phone hardware), `owner-account` (an account or sign-in only Roberto holds), `physical` (a physical action), `third-party` (a third-party system the session cannot reach) or `taste` (a taste or judgement call). A new page or view is browser-provable, so it never needs him: the lane proves it with a fresh visual proof. A criterion that only a later scheduled run can show is the chief's to read, never the owner's.

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
- proof: https://github.com/<owner>/<repo>/pull/<N>, <private artifact URL>
- related: #<M>, #<K>
- since: 2026-11-01
```

| Field | Rule |
| --- | --- |
| `kind` | `validate-after`, `before-start` or `cleared`. Required. |
| `reason` | Why it needs Roberto: the rule's reasons, joined with `; `. |
| `try` | What exactly to do: where to go, what to press, what should happen. |
| `proof` | Comma-separated links: the PR first, then any private artifact or proof page. |
| `related` | Other issues of the same piece of work (back-fill only). |
| `since` | UTC date the state began. |
| `answer` | Roberto's Board answer (`cleared` blocks only). |

Every value is one line. Empty fields are left out. Repos are public, so a block never carries a screenshot, a real path or personal content. Proof links point to private artifacts or the PR, and a private repo's block uses generic wording.

## The rule

`skills/_lib/owner_check/rule.py` decides `required` or `none` and lists every reason that fired. It only adds reasons; an explicit `none` never suppresses one.

| Clause | Applies to | Reads |
| --- | --- | --- |
| The issue says `Owner validation: required` | any type | the body's `Owner validation:` line |
| A real device or live input: microphone, mic, camera, webcam, WASAPI, dictation, voice notes, iPhone, iPad, Android, on-device, haptics, on-screen keyboard, home-screen, Bluetooth; silent once the lane passes `--verified yes` or `scheduled` | any type | the title |
| A changed live-input path (`wasapi`, `mic`, `microphone`, `camera`, `webcam` path tokens) | any type | the PR's files |
| A third party's account: sign-up, OAuth, subscription, `sign in with`, or a named provider's account or sign-in | enhancement | the title |
| Each named gap: a criterion the lane could not prove, with its owner-only class | any type | `finish --verified no --gap <class>:<criterion>` |

Words are matched in the title only. Issue bodies quote phone viewports, network loopback and "report" so often that body matching flagged more issues than there were enhancements. The app's own password sign-in is drivable with test credentials, so it never fires. A UI tweak a screenshot baseline proves needs nobody. A bug the lane fixed and re-verified in-session needs nobody either, unless its diff changes a live-input path. A title word is a guess made before the work exists, so recorded in-session proof outranks it.

`unknown` is its own state, never folded into `none`. The CLI produces it when the issue or the PR cannot be read, or when a required label or comment write did not land.

## Where it runs

Every `<repo>#<N>` argument (`gate`, `finish`, `mark`, `clear`) also takes the owner-qualified `<owner>/<repo>#<N>` that `gh` prints. The owner must be the fleet owner; any other owner, or any other shape, is refused with an error naming both accepted forms.

| Point | Command | Effect |
| --- | --- | --- |
| `/issue-add` step 6 | `owner_check classify --body-file <draft> --title "<title>" --label <type>` | The verdict for the template's `Owner validation:` line. The model writes only the why and the "try" text. |
| `/issue-start` step 2 | `owner_check gate <repo>#<N>` | `OWNER_GATE=blocked` stops with the reason and the try text. `unknown` also stops. |
| `/issue-finish` step 5, after merge | `owner_check finish <repo>#<N> --pr <PR> --verified yes\|scheduled\|no [--gap <class>:<criterion> …] --try "…" [--proof URL …]` | When required, it labels the issue `needs-validation` and posts the block. It writes nothing and exits 2 without `--verified`, for `no` without a valid `--gap`, for a `--gap` beside `yes`/`scheduled`, or without `--try` on a required verdict. An enhancement that adds a page (`.html`) or a view module (`views/*.js`, `*-view.js`) outside `_vendored/`, `tests/`, `docs/` and `fixtures/` is refused too until the PR body carries `VISUAL_PROOF=fresh`: a browser proves it, never the owner. A `none` re-run for the same PR removes the `needs-validation` label an earlier `finish` for that PR added and posts a `cleared` block (`RETRACTED=yes`); a hand mark or another PR's block is left alone. |
| Chief, before dispatch | `chief_ops.py dispatch` | Refuses a `needs-owner` issue. An unreadable issue only warns, because `/issue-start`'s gate is the hard stop. |
| Chief, reading | `chief_ops.py owner-checks` | Every open owner item fleet-wide, from the labels. |
| Chief, parking | `owner_check mark <repo>#<N> --kind before-start --reason "…" --try "…"` | Owner-blocked work gets the label first; `plan set --status parked --kind owner` then records it and is refused without the label. The `--try` text must name the exact owner action, where to do it and which answer unblocks the work, so the Board question answers cold; an empty, placeholder or under-six-word action is refused (exit 2). A block that is really an agent's decision goes to the chief, never the owner. |
| Board answer | `owner_check clear <repo>#<N> --answer "…"` | Removes the label and posts a `cleared` block with the answer. |

`--verified yes` means the original repro or acceptance re-ran and passed in this session. Pass `scheduled` when everything did except a criterion that only a later scheduled run can show: it adds no reason and prints `SCHEDULED_READ=chief`, the chief reading that run. Pass `no` only for a criterion the deterministic proofs cannot show, with one `--gap <class>:<criterion>` per such criterion; each becomes its own reason. A generic "could not verify" names nothing Roberto can act on, so `no` without a gap, and a missing `--verified`, are refused.

Run every command as `E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/owner_check/cli.py <command> …`.

## Exit codes and output

| Command | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| `classify`, `finish` | none | required (`APPLIED=yes` or `already`) | refused (`finish`): no `--verified`, `no` without a valid `--gap`, an unproven new page or view, or required with no `--try` | unknown |
| `gate` | clear | blocked | | unknown |
| `mark`, `clear`, `ensure-labels`, `backfill`, `reclassify`, `list` | every write or read landed | | refused (`mark`): a before-start step with no concrete owner action | something did not land; named on its own line |

Each command prints `KEY=value` lines (`OWNER_CHECK=`, `OWNER_GATE=`, `REASON=`, `TRY=`, `MARKED=`, `CLEARED=`, `UNKNOWN=`).

## Readers

`owner_check.list_items(repos=None)` returns `{"items": [...], "unknown": [{"repo", "reason"}]}`, one item per labelled issue (open or closed). Each item has `repo`, `number`, `ref`, `title`, `url`, `state`, `kind` and `block`, plus the block's `reason`, `try`, `proof` and `since`. A labelled issue with no matching block is still an item, with `block: false` and empty fields; a reader never invents them. A repo that could not be read lands in `unknown`, never in an empty list. A repo with GitHub Issues switched off holds no items and is not `unknown`. `owner_check list --json` prints the same shape. Items come newest `since` first, undated last, so the Board's "Waiting for start" and `chief_ops.py owner-checks` both put the most recent step on top and show its date.

GitHub's issue-list index trails a label write by a few seconds, so an item marked moments ago can be missing from `list` while the issue itself already carries the label. Read the issue (`gate`) when it has to be current to the second.

## Re-classifying the list

`owner_check reclassify [--repo R] [--apply]` re-runs the rule over every `needs-validation` issue and clears the label where no reason fires any more, with a `cleared` block that names the rule that stopped applying. A dry run prints `RECLASS keep|would-clear|unknown <ref>` per item and a `RECLASSIFY=` count line, and writes nothing. Three kinds of recorded reason are never recomputed, so they keep the item: a lane's named gap (a criterion with an owner-only class), any text a person wrote by hand, and any item whose PR cannot be read (`unknown`, never cleared). A generic "the lane could not verify" or "did not state" reason names no criterion and no class, so it drops. No in-session proof is inferred from history, so a recorded device reason still keeps its item. Everything else is recomputed from the issue title and body and the PR's files; a recorded new-page-or-view reason drops, since a browser proves it. A back-fill-only entry has no rule reason, so it clears.

## Back-fill

`owner_check backfill --list <file> --since <date> [--until <date>] [--apply]` merges two candidate sources:

- **An audit list.** Each `|`- or `;`-separated segment is one item: `repo#N`, ranges (`#10-12`) and `,#M` continuations, with the short names `fs`, `cm`, `al`, `hub`, `lite` and `grocery`.
- **The rule's dry run.** It covers issues closed as completed in the window, using the title clauses only, since history has neither a diff nor a verification statement.

An item spanning several issues is one Board entry. Its lowest-numbered issue carries the label and the block, with leading PR numbers skipped; the rest are listed in `related`. An issue that already has an owner-check label or block is skipped. The block's reason and try text are generic, so a private repo's content never reaches a comment. Without `--apply` it prints the plan and writes nothing.
