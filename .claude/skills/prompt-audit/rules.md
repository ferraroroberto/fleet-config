# /prompt-audit rule-set

The distilled, versioned standard `/prompt-audit` measures fleet instruction files against. Loaded on demand by the skill and its judgment agents — never from an always-on file. Its sha256 is the ledger's `rubric-sha`: editing this file forces a full rescan on the next run.

## How to read a rule

```
### R-NN Title        tags: [<vendor>] [file: <scope>] [tier: <tier>]
Detect: lint — <what audit.py counts>    |    Detect: judgment — <what the agent looks for>
Why: <the guide's reason, quoted or closely paraphrased>
Fix shape: <the smallest edit that resolves it>
Source: <page> · <page>
```

- **Vendor tag** — whose guidance the rule is. `[shared]`: both vendors → a `violation` in any file. A single-vendor tag: a `violation` only in a file or section scoped to that vendor's agent; in an agent-neutral file (read by several agents) it is at most `consider`; in a file scoped to the *other* vendor it does not apply. `[conflict]`: the vendors disagree → a `violation` only when a neutral file hardcodes one side outside an agent-neutrality marker; it does not apply inside a vendor-scoped section. Audience comes from the file, never from the host running the audit (`sources.toml` `[audiences.*]`).
- **File scope** — `any` (every instruction file, not a `skill-ref`); `claude-md` = the always-on instruction files (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/*.md`); `skill` = a `SKILL.md`; `skill-ref` = a `.md` file a `SKILL.md` references by markdown link or by a backticked relative path that exists, shared docs outside the skill directory included (never another skill's `SKILL.md`, an always-on file, or anything under `conversations/`, `evals/`, `__pycache__`).
- **Tier** — who can fix it unattended. `easy`: a mechanical rewrite (delete a line, soften a word, add a marker). `hard`: restructuring, removing a step list, resolving a contradiction, or any edit to `global-CLAUDE.md` / `project-scaffolding/CLAUDE.md` regardless of the rule's own tier.
- **Detect** — `eval` rules are measured by the weekly skill-eval job and read by `audit.py evals`; `lint` rules are counted exactly by `audit.py lint` (a hit is a candidate, confirmed or rejected by the judgment pass); `judgment` rules have no mechanical signal and are assessed by the judgment agent alone; `judgment, lint-assisted` rules are judged, but `audit.py lint` nominates candidates for them the same way. A Detect line saying "Consider-only for its first weekly cycle" caps every verdict at `consider` (lint and judgment) until that dated sentence is deleted — the noise guard for a new rule likely to fire widely.
- **Verdicts** — per rule, per file: `violation`, `consider`, `compliant`, or `unmeasured` (not established — never folded into `compliant`).
- **Prose is vendor-neutral.** Vendors are named only in tags and `Source:` lines; a quoted guide sentence says "[the model]" where the guide names one.

Text inside fenced code blocks and inline code spans is not linted: a fence is a command or a quoted example, not an instruction to the reader.

## Remove — scaffolding written for older models

### R-01 No ALL-CAPS pressure        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — occurrences of the capitalised words MUST, NEVER, ALWAYS, CRITICAL, IMPORTANT, REQUIRED, MANDATORY, NOT, DON'T (headings included).
Why: current models are more responsive to the system prompt, so language written to fix undertriggering now overtriggers: "Where you might have said 'CRITICAL: You MUST use this tool when...', you can use more normal prompting like 'Use this tool when...'."
Fix shape: lower-case the word and keep the sentence; where the emphasis carried a real priority, state the reason instead (R-18).
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Tool usage)

### R-02 No blanket tool defaults        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "if in doubt", "when in doubt", "default to using / calling / running / invoking".
Why: "Instructions like 'If in doubt, use [tool]' will cause overtriggering." Replace a blanket default with a targeted condition: "Use [tool] when it would enhance your understanding of the problem."
Fix shape: rewrite as the concrete condition under which the tool helps, or delete.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Overthinking and excessive thoroughness)

### R-03 No forced verification or re-check lines        tags: [shared] [file: any] [tier: easy]
Detect: lint — "double-check", "re-verify", "verify your answer/work/output/response/changes", "final verification step", "subagent to verify", "re-check your". Judgment clears a candidate that names a real check to run (the project's tests, type-checker or build, or the changed command) rather than asking for a generic re-check.
Why: current models verify their own work unprompted; explicit verification instructions "cause over-verification … and removing them reduces wasted tokens with no loss in quality". Self-correction prompts like "double-check your answer" "compound with the model's own behavior and add cost without improving results". The other vendor's flagship guide makes the same point for coding: calibrate testing, because the model "tends to be thorough in testing" and over-tests small changes. The exception the guides carve out is a named real check: at low effort a smaller model "sometimes reports a change as done without running a check that exercises it", and the suggested text is "run a real check that exercises the change before reporting it done: the project's tests, type-checker, or build, or the changed command itself. A syntax-only check, or a check command that failed to start, does not count".
Fix shape: delete the generic line. If one specific check is genuinely required (a named gate command), name that check once, without "double-check"; an instruction to run the real check before reporting done is compliant.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Task scope and over-verification; Self-correction) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Verification on coding tasks) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 (Tell coding agents to verify their changes) · https://developers.openai.com/api/docs/guides/latest-model (Testing and verification)

### R-04 No periodic progress-summary scaffolding        tags: [shared] [file: any] [tier: easy]
Detect: lint — "every N tool calls", "every few tool calls", "after every N steps".
Why: "If you've added scaffolding to force interim status messages ('After every 3 tool calls, summarize progress'), try removing it." The coding-model guide agrees: "remove all prompting for the model to communicate an upfront plan, preambles, or other status updates during the rollout, as this can cause the model to stop abruptly before the rollout is complete." What the guides endorse instead is a harness-injected reminder: "After several in a row (five, for example), append a reminder", sent as a turn-scoped system message and dropped "after the second or third".
Fix shape: delete; if updates matter to a human reader, say what an update should contain, not how often. A harness-injected reminder that fires after N silent steps is a turn-scoped mechanism, not instruction-file prose — R-04 still applies to a static "every N tool calls" line.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (User-facing progress updates) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (User-facing progress updates) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (User-facing progress updates) · https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide (Getting Started)

### R-05 No narration suppression        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "hold (all) findings/results/updates/output for/until the final …".
Why: "Some earlier models were eager to give updates while working, which led to system prompt lines such as 'hold all findings for the final response.' Remove lines like that before adding anything."
Fix shape: delete the line.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Ask for user-facing progress updates) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (User-facing progress updates)

### R-06 No blanket anti-formatting blocks        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — lines that forbid markdown, bullets, headers, headings, bold, lists or formatting ("do not use markdown", "never use bullet points", "no headers").
Why: "Earlier models overused bullets and bold in chat, and many prompts carry anti-formatting rules written to hold that down." Current models lean the other way: "If your prompt contains anti-formatting language, remove it or replace it with a rule that says when specific formatting is appropriate." Positive framing works better too: instead of "Do not use markdown", say what the output should be.
Fix shape: replace with the positive rule for *when* a format is appropriate; a format rule with a real downstream reason (a renderer, a wire format) stays, with the reason.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Formatting in chat) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Control the format of responses)

### R-07 No hand-written reasoning plans        tags: [anthropic] [file: any] [tier: hard]
Detect: lint — "think step by step", "step-by-step reasoning/thinking", "reason step by step", and numbered list items that open with think / reason / reflect.
Why: "A prompt like 'think thoroughly' often produces better reasoning than a hand-written step-by-step plan. [The model]'s reasoning frequently exceeds what a human would prescribe." Depth is an effort setting, not a prose setting: "Asking it in the system prompt to think less doesn't reliably reduce its thinking", and for chat prompts "The model determines how much to think, and effort is the main control." A short think-first line is a measured per-model patch, not a default: on structured-output reasoning tasks at `high` effort "Think the problem through before you answer." "brings accuracy close to what the model reaches at `xhigh`".
Fix shape: replace the plan with a one-line goal and leave depth to effort. A single think-first line is acceptable only where the file names the model and task it was measured on (structured-output reasoning); in a chat system prompt or an agent-neutral file, delete rather than rewrite it. Keep numbered steps only where they are an operational procedure (commands in order), not a reasoning script.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Leverage thinking & interleaved thinking capabilities) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (Thinking instructions in chat system prompts) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Calibrate effort; Reasoning tasks with JSON output)

### R-08 No "do not think" rules        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "do not / don't / never think" or "… reason" (not followed by about / of / that).
Why: "If your system prompt contains a rule instructing the model not to think or not to reason, remove it; that kind of instruction increases tag leakage." The same holds when thinking is minimised rather than off: "remove any instruction that tells the model not to think. Such instructions make it more likely that the model writes internal XML tags in its visible output."
Fix shape: delete; control depth through effort, not prose. Where thinking is always on, or can only be minimised, a "do not think" line is dead text that still raises tag leakage.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Running with thinking disabled) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Running without up-front thinking) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 (Use effort to control thinking) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Leverage thinking & interleaved thinking capabilities)

### R-09 No recall-suppressing review instructions        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "only report high-/critical-severity", "be conservative", "don't nitpick".
Why: "If your review prompt says 'only report high-severity issues' or 'be conservative,' the model may follow that instruction literally and report less; ask it to report everything and filter in a separate pass instead."
Fix shape: ask for every finding with a severity/confidence label; filter in a separate step.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Capability improvements) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (Code review harnesses)

### R-10 No prefill or thinking-budget references        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "prefill" (any form), "budget_tokens".
Why: prefilled last assistant turns "are no longer supported" and return a 400 error on current models; `budget_tokens` "is deprecated" and returns a 400 on newer models — "move budget control to `effort`".
Fix shape: delete the instruction or replace with the effort-based equivalent.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Migrating away from prefilled responses; Overthinking and excessive thoroughness; Leverage thinking & interleaved thinking capabilities; Migrating to Claude Sonnet 5.5)

### R-11 No time-sensitive phrasing        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "before / after / until / since / as of / by <Month> <year>".
Why: "Avoid time-sensitive information" — an instruction that says "before August 2025 use the old API" is wrong the day after and nobody notices. Put deprecated guidance in a clearly labelled "old patterns" section instead.
Fix shape: remove the date condition, or move the legacy case into a labelled legacy/decision-log section. Dated decision-log bullets and README rotation dates are records, not conditions, and are compliant.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Avoid time-sensitive information)

### R-12 No upfront-plan or preamble demands        tags: [openai] [file: any] [tier: easy]
Detect: lint — "outline / present / share / state / write a (upfront) plan before …", "upfront plan", "begin each response with a plan".
Why: "remove all prompting for the model to communicate an upfront plan, preambles, or other status updates during the rollout, as this can cause the model to stop abruptly before the rollout is complete."
Fix shape: delete. A human approval gate (plan mode) is a workflow step, not a preamble demand, and is compliant.
Source: https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide (Getting Started)

## Add or keep — what current guidance asks for

### R-13 Positive framing over negatives        tags: [anthropic] [file: any] [tier: hard]
Detect: lint — lines containing never / do not / don't / avoid / must not (count), reported with the ratio to all instruction lines.
Why: "Tell [the model] what to do instead of what not to do." Positive examples showing the wanted behaviour "tend to be more effective than negative examples or instructions that tell the model what not to do."
Fix shape: rewrite the rule as the behaviour wanted, keeping a negative only where it names a real hazard (a destructive command); never mass-rewrite — judgment decides per line. Exception: a named anti-pattern list for design output is compliant — the model "responds well to instructions that name specific patterns to avoid".
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Control the format of responses) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (Response length and verbosity) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (Frontend design defaults)

### R-14 Instruction-file size cap        tags: [shared] [file: any] [tier: hard]
Detect: lint — `CLAUDE.md` / `.claude/rules/*.md` over 200 lines; `SKILL.md` body over 500 lines; `AGENTS.md` over 32768 bytes.
Why: "target under 200 lines per CLAUDE.md file. Longer files consume more context and reduce adherence." "Keep SKILL.md body under 500 lines for optimal performance." The `AGENTS.md` loader "stops adding files once the combined size reaches the limit defined by `project_doc_max_bytes` (32 KiB by default)" — past the cap, instructions are silently truncated.
Fix shape: move procedures and scoped detail into skills, path-scoped rules or referenced docs (R-26); never compress by deleting directives — `/context-purge` owns lossless compression. An `@path` import does not shrink the budget: "Splitting into `@path` imports helps organization but doesn't reduce context, since imported files load at launch."
Source: https://code.claude.com/docs/en/memory (Write effective instructions; Troubleshoot memory issues → My CLAUDE.md is too large) · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Token budgets) · https://developers.openai.com/codex/guides/agents-md

### R-15 Skill description: short, third person        tags: [anthropic] [file: skill] [tier: easy]
Detect: lint — description over 1024 characters; first- or second-person words (I, me, my, we, our, you, your) in the description prose, with quoted trigger phrases excluded; an XML tag in the description; a `name` that is not 1–64 characters of lowercase letters, digits and hyphens. Prose word count is reported against the fleet's 50-word cap (owned by `/context-audit`). Judgment adds: the description says both what the skill does and when to use it.
Why: the description "is injected into the system prompt, and inconsistent point-of-view can cause discovery problems" — "Always write in third person." The field has a 1024-character maximum and "cannot contain XML tags"; `name` is "Maximum 64 characters" of "lowercase letters, numbers, and hyphens only". "The description should include both what the Skill does and when to use it."
Fix shape: rewrite the prose in third person; leave quoted trigger phrases verbatim (they are user utterances and the routing surface); drop the tag or reword it as prose; rename to the allowed charset; add the missing what or when clause.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Writing effective descriptions; YAML frontmatter requirements; Core quality)

### R-16 No contradictory rules        tags: [shared] [file: any] [tier: hard]
Detect: lint — within one file, the same action phrase under both an always/must and a never/do-not modal ("always commit to X" vs "never commit to X"). Judgment adds contradictions the pattern cannot see, including across the global file and a project file.
Why: "if two instructions contradict each other, [the model] may pick one arbitrarily. Review your CLAUDE.md files, nested CLAUDE.md files in subdirectories, and `.claude/rules/` periodically to remove outdated or conflicting instructions." Across scopes too: "Neither set overrides the other: if a user rule and a project rule conflict, [the model] may follow either one, so keep the two consistent." The other vendor: "unclear or conflicting guidance in a skill file may cause the model to pause and block work early."
Fix shape: resolve to one rule with its scope stated ("in a worktree …"); record which one won in the owning issue's decision log.
Source: https://code.claude.com/docs/en/memory (Write effective instructions; User-level rules) · https://developers.openai.com/api/docs/guides/latest-model (Instruction following)

### R-17 Vendor-specific guidance carries a neutrality marker        tags: [shared] [file: any] [tier: easy]
Detect: lint — in an agent-neutral file or section, a paragraph that names one audience's product terms (`sources.toml` `terms`) and none of the other's, without any audience marker.
Why: a neutral file is read by several agents; guidance tuned for one model is noise or harm for another. Both vendors stress auditing instruction files for guidance that does not apply to the reader, and the per-model guides carry their own hedge: a technique measured on one model is re-checked before being applied to another.
Fix shape: add the audience marker listed in `sources.toml` to the heading, or move the paragraph to the vendor-scoped file. A paragraph that merely names a product as data (a model id in a routing table) is compliant.
Source: https://developers.openai.com/api/docs/guides/latest-model (Instruction following) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (General principles)

### R-18 Give the reason behind the rule        tags: [anthropic] [file: any] [tier: hard]
Detect: judgment — a directive whose motivation is neither stated nor linked (an issue number or a one-clause "because" counts).
Why: "Providing context or motivation behind your instructions, such as explaining to [the model] why such behavior is important, can help [the model] better understand your goals and deliver more targeted responses." A reasoned rule generalises to cases the rule's wording missed.
Fix shape: add one clause of why, or the issue link that holds it.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Add context to improve performance)

### R-19 Explicit scope boundary        tags: [shared] [file: any] [tier: hard]
Detect: judgment — a task-shaped instruction (a skill, a workflow section) with no statement of what is in and out of scope.
Why: current models "can also expand the scope of a task, adding steps that weren't requested"; "Deliver what was asked, at the scope intended." The other vendor: "infer the user's intent and task scope from the instructions and prior conversation context." And the scope is the deliverable: "The user's request — or the plan they approved — sets the scope, and the scope is the deliverable: don't quietly narrow, widen, or swap it."
Fix shape: one sentence naming the deliverable and what is explicitly out of scope.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Task scope and over-verification) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Finish the whole task) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Steer initiative and scope) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 (Prevent early stopping in long agent prompts) · https://developers.openai.com/api/docs/guides/latest-model (Initiative and follow-through)

### R-20 Explicit length calibration        tags: [shared] [file: any] [tier: hard]
Detect: judgment — an instruction that produces a written artefact (report, issue body, digest, summary) with no length or density guidance.
Why: effort controls how much the model thinks, not how long it writes; written deliverables "are often longer than on prior models … add explicit length calibration". The other vendor: the model "tends toward detailed, formatted responses … Specify the writing style and structure your application needs."
Fix shape: one line of length/density calibration for that artefact.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Response length and verbosity; Written deliverable length) · https://developers.openai.com/api/docs/guides/latest-model (Personality and writing style)

### R-21 Autonomy with an assessment carve-out        tags: [shared] [file: any] [tier: hard]
Detect: judgment — an unattended or long-running workflow that does not say to carry reversible work through without asking, or that has such a block without the "a question is answered with an assessment, not a fix" exception.
Why: the autonomy block — "The user is not watching in real time … For reversible actions that follow from the original request, proceed without asking" — is paired with "when the user is describing a problem, asking a question, or thinking out loud … the deliverable is your assessment." The other vendor: "bias towards action and carry the user's intended task to completion" unless "clearly destructive or irreversible". When a question comes up partway, the workflow does everything that doesn't depend on the answer first, then states the assumption made — or asks at the end of a turn that also delivers progress.
Fix shape: add the missing half; keep the opening sentence as written.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Finish the whole task) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Steer initiative and scope) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 (Prevent early stopping in long agent prompts) · https://developers.openai.com/api/docs/guides/latest-model (Initiative and follow-through)

### R-22 Reversibility gate names the destructive actions        tags: [shared] [file: any] [tier: hard]
Detect: judgment — a "confirm before destructive actions" rule that does not enumerate what counts as destructive in this context.
Why: "Stop only for destructive actions or genuine scope changes the user must decide"; "check that the evidence actually supports that specific action" before a state-changing command. The other vendor: "You don't need user permission for reversible tasks, read-only actions, reviews or fixes". An unenumerated gate either blocks routine work or waves through the one action that mattered.
Fix shape: list the concrete destructive actions (delete, force-push, restart a live service, …).
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Finish the whole task) · https://developers.openai.com/api/docs/guides/latest-model (Initiative and follow-through)

### R-23 Parallel independent tool calls        tags: [shared] [file: any] [tier: hard]
Detect: judgment — a workflow that forces independent reads or checks to run one at a time with no ordering reason.
Why: both vendors recommend batching independent calls — "Optimize parallel tool calling"; "When multiple tool calls can be parallelized … make these tool calls in parallel instead of sequential."
Fix shape: say which steps are independent and may run together; keep sequencing only where one step consumes another's output or a shared resource forbids overlap (and say which).
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Optimize parallel tool calling) · https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide (General)

### R-24 Examples: few, diverse, tagged        tags: [anthropic] [file: skill] [tier: hard]
Detect: judgment — a skill whose output format is only described, where one wrong shape costs a rerun; or examples that are all near-identical.
Why: "Include 3–5 examples for best results", wrapped in `<example>` tags, diverse enough that the model does not copy an incidental detail.
Fix shape: add or diversify examples, tagged.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Use examples effectively)

### R-25 Structure mixed content        tags: [anthropic] [file: any] [tier: hard]
Detect: judgment — instructions, reference data and examples interleaved in one undifferentiated block.
Why: "XML tags help [the model] parse complex prompts unambiguously, especially when your prompt mixes instructions, context, examples, and variable inputs." For instruction files: "group related instructions under markdown headers and bullets" — "Organized sections are easier for [the model] to follow than dense paragraphs".
Fix shape: separate the kinds under headings or tags.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Structure prompts with XML tags) · https://code.claude.com/docs/en/memory (Write effective instructions)

## Instruction-file hygiene

### R-26 Procedures belong outside the always-on file        tags: [shared] [file: claude-md] [tier: hard]
Detect: judgment — a multi-step procedure, or guidance that only matters for one part of the codebase, living in an always-on file.
Why: "If an entry is a multi-step procedure or only matters for one part of the codebase, move it to a skill or a path-scoped rule instead." The `AGENTS.md` guide: "split instructions across nested directories" rather than one file that hits the size cap.
Fix shape: move the procedure to a skill or a scoped file and leave a one-line pointer.
Source: https://code.claude.com/docs/en/memory · https://developers.openai.com/codex/guides/agents-md

### R-27 Instruction files advise; hooks and CI enforce        tags: [shared] [file: claude-md] [tier: hard]
Detect: judgment — an always-on rule stated as an absolute guarantee ("this can never happen") that no hook, test or CI check actually enforces.
Why: instruction files are "context, not enforced configuration" — "there's no guarantee of strict compliance". "If the instruction is something that must run at a specific point … write it as a hook instead." A guarantee that nothing enforces teaches readers to trust it.
Fix shape: name the enforcing hook/check, or reword as guidance and file the enforcement gap.
Source: https://code.claude.com/docs/en/memory (CLAUDE.md vs auto memory; Write effective instructions; Troubleshoot memory issues → Claude isn't following my CLAUDE.md) · https://developers.openai.com/codex/guides/agents-md

### R-28 Subagent delegation stance stays scoped        tags: [conflict] [file: any] [tier: hard]
Detect: judgment — an agent-neutral file that tells the reader to delegate more, or less, without a vendor scope marker.
Why: the vendors disagree. One: "Delegate to a subagent only for large tasks that are genuinely independent and parallelizable … do not use subagents to verify or double-check your own work." The other: the flagship "may delegate less often than desired … Specify when and how much it should use subagents for parallel work." A neutral file that hardcodes either side is wrong for one reader.
Fix shape: move the stance under a vendor-scoped marker, or state the condition (task shape, cost cap) rather than a direction.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Controlling subagent spawning) · https://developers.openai.com/api/docs/guides/latest-model (Subagent delegation)

### R-29 User instructions outrank skills, explicitly        tags: [openai] [file: skill] [tier: easy]
Detect: judgment — a skill with gates that pause or refuse, and no statement that an explicit user instruction takes precedence.
Why: the flagship "is better able to follow longer instructions, but can also be more sensitive to information in context … Make the priority of user instructions and skills explicit." Unclear precedence makes it pause and block work early.
Fix shape: one sentence on precedence, scoped to what the skill must still never do (its safety gates).
Source: https://developers.openai.com/api/docs/guides/latest-model (Instruction following)

### R-30 No reasoning-in-the-answer instructions        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "explain/show your reasoning in the response", "write out your reasoning", "show your work", "think out loud in your answer".
Why: "Remove instructions that stood in for thinking. If your prompt asked the model to write out its reasoning in the response as a substitute for thinking, remove that instruction … a prompt that pushes the model to reproduce its reasoning in the response text may be declined with the `reasoning_extraction` refusal category."
Fix shape: delete the line; if the reasoning is needed downstream, read the summarized thinking instead.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (Prompts written for thinking disabled; Safeguard refusals) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Safeguard refusals)

### R-31 A text-only stop is a report, not completion        tags: [anthropic] [file: any] [tier: hard]
Detect: judgment — an unattended or scheduled workflow that treats the agent's turn ending as proof the task finished, with no completion condition or open-items checklist, or that re-prompts a stalled run without a cap.
Why: "Treat a text-only end of turn as a report rather than as proof the task is done. Keep the task's parts in a checklist the model updates … stop after two or three automatic continuations on the same task rather than repeating them indefinitely, so that a run that is genuinely stuck ends and can be reviewed."
Fix shape: state the completion condition up front (the delivery assertion), keep a checklist of parts, and cap automatic continuations at two or three.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (Unattended agentic runs)

### R-32 A blocking skill names itself        tags: [openai] [file: skill] [tier: easy]
Detect: judgment — a skill that makes the reader pause, ask or refuse, with no instruction to cite the gate (skill file and the instruction quoted) when it does.
Why: "If a skill causes you to ask for permission or confirmation, pause, leave requested work unfinished, or diverge from the user's intent, name and link to the exact SKILL.md file you read, quote the relevant instruction, and briefly explain how it applies. Distinguish explicit skill requirements from your interpretation of guidelines."
Fix shape: one line beside the gate: when it stops, it names the skill and quotes the rule.
Source: https://developers.openai.com/api/docs/guides/latest-model (Instruction following)

### R-33 No invented gates or disclaimers        tags: [openai] [file: any] [tier: hard]
Detect: judgment — approval flows, warnings or compliance checklists that answer a hypothetical risk rather than a named destructive action (complements R-22).
Why: "Do not introduce unsolicited warnings, disclaimers, approval flows, or safety/compliance checklists due to hypothetical risk."
Fix shape: delete the gate, or tie it to the concrete destructive action R-22 enumerates.
Source: https://developers.openai.com/api/docs/guides/latest-model (Initiative and follow-through)

## Skill structure

How a skill's files are laid out, so the reader finds what it needs when it partially reads them. R-34 to R-37 are its lint half; R-38 to R-43 its judgment half.

### R-34 Reference files one level deep        tags: [anthropic] [file: skill] [tier: hard]
Detect: lint — a reference file (a `.md` that SKILL.md references by markdown link or by a backticked relative path that exists, inside the skill directory or a shared doc outside it; another skill's SKILL.md is delegation, not a reference) which itself links (markdown link) another local `.md` that SKILL.md does not reference directly. A backticked path inside a reference file is a mention, not an onward reference.
Why: "[The model] may partially read files when they're referenced from other referenced files … resulting in incomplete information." "Keep references one level deep from SKILL.md."
Fix shape: reference the leaf file from SKILL.md directly, or fold it into its parent.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Avoid deeply nested references)

### R-35 Long reference files open with a contents list        tags: [anthropic] [file: skill-ref] [tier: easy]
Detect: lint — a reference file (see R-34) over 100 lines with no Contents / Table of contents heading in its first 40 lines.
Why: "For reference files longer than 100 lines, include a table of contents at the top. This ensures [the model] can see the full scope of available information even when previewing with partial reads."
Fix shape: add a `## Contents` list of the file's sections under its title.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Structure longer reference files with table of contents)

### R-36 Forward-slash paths        tags: [anthropic] [file: skill] [tier: easy]
Detect: lint — a backslash path outside code fences. A relative skill-file path (`scripts\x.py`) caps at violation; an absolute drive path (`E:\automation\…`) or a Windows venv path (`.\.venv\Scripts\python.exe`) caps at consider, because it is a host-local fact and forward slashes also work on Windows.
Why: "Always use forward slashes in file paths, even on Windows … Windows-style paths cause errors on Unix systems." The skills are junctioned into every other agent harness, and the lite port runs on another machine.
Fix shape: rewrite with forward slashes (`E:/automation/...`); leave fenced `.bat`/cmd bodies alone.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Avoid Windows-style paths; Runtime environment)

### R-37 MCP tools named fully qualified        tags: [anthropic] [file: skill] [tier: easy]
Detect: lint — a backticked bare tool name `T` where `mcp__<server>__T` appears anywhere in the fleet's skills (a self-maintaining vocabulary), on a line that does not carry the qualified form. The host's own qualified form (`mcp__server__tool` in the host harness) counts as qualified; the guide's `Server:tool` is the API spelling.
Why: "always use fully qualified tool names to avoid 'tool not found' errors … Without the server prefix, [the model] may fail to locate the tool, especially when multiple MCP servers are available."
Fix shape: use the qualified name, at least on each first mention per section.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (MCP tool references)

### R-38 Degrees of freedom match fragility        tags: [anthropic] [file: skill] [tier: hard]
Detect: judgment — a fragile, order-sensitive operation (destructive git, a migration, a wire format) given only as loose prose; or an exact script imposed where several approaches are valid.
Why: "Match the level of specificity to the task's fragility and variability."
Fix shape: pin the fragile step to an exact command or bundled script; loosen the rest to a goal plus heuristics.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Set appropriate degrees of freedom)

### R-39 Long workflows give a copyable checklist        tags: [anthropic] [file: skill] [tier: hard]
Detect: judgment, lint-assisted — lint counts top-level steps; a workflow of six or more steps with no checklist the agent can copy and tick is a candidate.
Why: "For particularly complex workflows, provide a checklist that [the model] can copy into its response and check off as it progresses."
Fix shape: add a `- [ ]` checklist that mirrors the step headings.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Use workflows for complex tasks)

### R-40 Quality-critical steps loop on a machine check        tags: [anthropic] [file: skill] [tier: hard]
Detect: judgment — a quality-critical or destructive step with no validate → fix → repeat loop on a named check (a gate command, a validator script, a plan file validated before execution).
Why: "Common pattern: Run validator → fix errors → repeat. This pattern greatly improves output quality." Plan-validate-execute is for "Batch operations, destructive changes, complex validation rules, high-stakes operations."
Fix shape: name the check and the loop ("run X; on failure fix and re-run until it passes, at most N times"). Not a conflict with R-03, which removes self-re-check prose; R-03's fix shape already says to name the one real check.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Implement feedback loops; Create verifiable intermediate outputs)

### R-41 One term per concept        tags: [anthropic] [file: skill] [tier: hard]
Detect: judgment — the same concept named with several terms in one skill (gate / check / verification step used interchangeably).
Why: "Choose one term and use it throughout the Skill … Consistency helps [the model] parse and follow instructions."
Fix shape: pick the term the skill's helpers print, and replace the synonyms.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Use consistent terminology)

### R-42 A default, not a menu        tags: [anthropic] [file: skill] [tier: easy]
Detect: judgment — several interchangeable tools or approaches offered with no default and no condition for the alternative.
Why: "Don't present multiple approaches unless necessary … Provide a default (with escape hatch)."
Fix shape: name the default; keep one alternative with the condition that selects it.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Avoid offering too many options)

### R-43 Bundled scripts: intent and dependencies stated        tags: [anthropic] [file: skill] [tier: easy]
Detect: judgment, lint-assisted — each bundled script SKILL.md references says whether to run it or read it; lint lists the non-stdlib imports of bundled scripts that SKILL.md does not name.
Why: "Make clear in your instructions whether [the model] should: Execute the script … Read it as reference"; "List required packages in your SKILL.md and verify they're available."
Fix shape: "Run `x.py` to …" or "See `x.py` for …"; one line naming any third-party package.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Provide utility scripts; Package dependencies; Avoid assuming tools are installed)

### R-44 Skill passes its evals on the tiers it runs on        tags: [anthropic] [file: skill] [tier: hard]
Detect: eval — in the weekly eval job's newest aggregate, a case that passed in the previous aggregate fails now. On a scored tier it is a violation; on the advisory (smallest) tier a consider. A missing, unreadable or stale aggregate is not-checked, never a pass.
Why: "Skills act as additions to models, so effectiveness depends on the underlying model. Test your Skill with all the models you plan to use it with." The page's checklist asks for "At least three evaluations created" and testing on each model tier.
Fix shape: read the failing case's transcript, then tighten the step the model got wrong; for an advisory-tier-only failure, add the missing guidance without over-explaining for the larger tiers.
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Test with all models you plan to use; Build evaluations first; Testing)

## Tool use

When the reader reaches for a tool. R-02 owns the blanket default to a tool; R-45 its mirror, a blanket limit on one.

### R-45 No blanket tool-use discouragement        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "only use tools when strictly necessary", "minimize tool calls", "minimise tool calls", "avoid (using|calling) tools unless". Consider-only for its first weekly cycle (the run on or after 2026-10-22), because a phrase can be a real cost cap on a scheduled job rather than a blanket default: it becomes a normal rule on 2026-10-29, when this sentence is deleted.
Why: on chat and knowledge-work tasks the model "sometimes answers from its training knowledge when a web search would catch details that have changed"; the first fix is to "check your prompt for language that discourages tool use, such as 'only use tools when strictly necessary' or 'minimize tool calls', and remove it." The mirror is also wrong: a blanket "search for any present-day factual question, regardless of how confident you are" made the model "search on half of the prompts that needed no search" with no more correct answers.
Fix shape: replace with the concrete condition under which the tool is worth its cost (details that may have changed since training, a named expensive call), or delete; a hard cost cap on a scheduled job states the cap and its reason (R-18).
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Tool use in chat and knowledge work) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 (Accurate search results)

## Long runs and research

Where a workflow keeps its progress, and what it reads before it acts or answers. R-31 owns when a run is done; these own what it carries between contexts and what it grounds a claim on.

### R-46 Long runs keep their state outside the context window        tags: [anthropic] [file: skill] [tier: hard]
Detect: judgment — a long-running or multi-session workflow (a queue, a migration, many units of work) that keeps its progress only in the conversation: no progress file, structured status file or git checkpoint it updates, and no line saying what a fresh context reads first to resume. Consider-only for its first weekly cycle (the run on or after 2026-10-22), because many fleet skills finish in one context and need no state file: it becomes a normal rule on 2026-10-29, when this sentence is deleted.
Why: for tasks spanning context windows, "Use structured formats for state data", "Use unstructured text for progress notes", and "Use git for state tracking: Git provides a log of what's been done and checkpoints that can be restored." Current models "are extremely effective at discovering state from the local filesystem", so "Be prescriptive about how it should start" — for example "Review progress.txt, tests.json, and the git logs." An instruction given only in conversation is the kind that disappears after compaction; one in a file is read again.
Fix shape: one line naming the state file (or the git log) the run reads on start and updates after each unit; structured data (statuses, results) in JSON, free-form notes in text.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Workflows across multiple context windows; State management best practices; Context awareness and multiwindow workflows) · https://code.claude.com/docs/en/memory (Instructions seem lost after `/compact`)

### R-47 Research workflows state their bar and stopping rule        tags: [shared] [file: skill] [tier: hard]
Detect: judgment — a skill that researches (the web, issues, code, ledgers) with no statement of what a successful answer is, what support a key claim needs, or when to stop searching. Consider-only for its first weekly cycle (the run on or after 2026-10-22), because a lookup skill with one obvious answer needs none of this: it becomes a normal rule on 2026-10-29, when this sentence is deleted.
Why: "Provide clear success criteria: Define what constitutes a successful answer to your research question", and "Encourage source verification: Ask [the model] to verify information across multiple sources." The other vendor: "Specify the research bar up front" — whether to follow second-order leads, resolve contradictions and include citations — and "Explicitly state how far to go, for instance: that additional research should continue until marginal value drops."
Fix shape: one or two lines — the success criterion, the support a key claim needs, and the stopping rule ("stop when another search is unlikely to change the answer").
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Research and information gathering) · https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (9. Web search and research)

### R-48 Read the sources before acting or claiming        tags: [shared] [file: skill] [tier: hard]
Detect: judgment — a workflow that changes external state (an issue, a PR, a record, a running app) or answers questions about code with no step that reads the relevant sources first, or one that tells the reader to fill a gap from memory instead of reading, asking, or stating the assumption. Consider-only for its first weekly cycle (the run on or after 2026-10-22), because most fleet skills already open with a read step and the judgment needs calibrating: it becomes a normal rule on 2026-10-29, when this sentence is deleted.
Why: "Never speculate about code you have not opened" — "Make sure to investigate and read relevant files" before answering about the codebase. On loosely specified tasks across several sources, "the information a task depends on often sits somewhere the request doesn't explicitly mention", and the measured fix is one sentence: "Before taking any action, explore broadly with tool calls". The other vendor: "Never fabricate exact figures, line numbers, or external references when you are uncertain." Exploring costs tool calls, and the model acts on what it finds, so "keep untrusted content out of the records it searches."
Fix shape: add the read step and name its sources ("read X and Y before drafting Z"), or the line that an unverified fact is reported as unverified. Not a blanket "always investigate" line.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Minimizing hallucinations in agentic coding) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (Explore context in multi-app workflows) · https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (3.4 Handling ambiguity & hallucination risk)

## Covered elsewhere

Guidance on a tracked page that another owner already measures, so no rule here duplicates it.

- **Script code quality** — solve errors instead of deferring them, no unexplained ("voodoo") constants, explicit error handling. That is code, not instruction prose: `/codebase-audit`'s job (its Maintainability bucket names magic constants, fleet-config#1230).
- **Concise is key** — `/context-purge` (lossless compression) and R-14 (size caps).
- **Progressive disclosure** — R-14's fix shape (move detail into referenced files) and R-34 (keep those references one level deep).
- **Template and example patterns** — R-24.
- **Testing and evaluation** — the skill evals (`/prompt-audit`'s eval steps), not lint.

Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Solve, don't defer; Code and scripts) · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Concise is key) · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Progressive disclosure patterns; Visual overview: From simple to complex; Pattern 1: High-level guide with references; Pattern 2: Domain-specific organization; Pattern 3: Conditional details) · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Template pattern; Examples pattern) · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Test with all models you plan to use; Build evaluations first; Develop Skills iteratively with Claude; Observe how Claude navigates Skills; Testing)

Per-model and per-vendor pages repeat guidance the rules above already own:

- **Autonomy and reversibility** — R-22: enumerate the destructive and hard-to-reverse actions; local, reversible work needs no gate.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Balancing autonomy and safety)
- **Subagent overuse** — R-28 owns the delegation stance; the page's damping prompt is one way to state the condition.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Subagent orchestration)
- **Over-engineering and unrequested tests** — R-19 (deliver the scope asked for); committing a test only where the task asks for one is the same boundary applied to test files.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Overeagerness) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Keep changes and tests to what the task asks for)
- **Named anti-pattern lists for design output** — R-13's exception.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Frontend design)
- **Parallel tool calls** — R-23; in long loops the nudge goes in a turn-scoped message (appendix).
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Batch independent tool calls in agent loops) · https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (6. Tool-calling and parallelism) · https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide (Parallel Tool Calling)
- **Worked examples** — R-24; a page that recommends one complete worked example instead of several is a per-task variation, not a conflict.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Quoting retrieved sources) · https://developers.openai.com/api/docs/guides/prompt-engineering (Few-shot learning)
- **Progress updates** — R-04 bans a count-based cadence, R-05 narration suppression and R-12 upfront-plan and preamble demands. An event-based rule (update when something important turns up or the plan changes) is what these pages recommend, and it is compliant with all three.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (User-facing progress updates) · https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (5. Agentic steerability & user updates) · https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide (Mid-Rollout User Updates)
- **Reasoning in the response** — R-30; the previous model's page carries the same decline category.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Reasoning in the response)
- **Tool-use triggering** — R-02; with thinking off a deliberate nudge to use tools is needed, which is an API-side condition.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (Tool use triggering)
- **Effort before prompt prose** — R-07 and the effort entry in the appendix: raise effort rather than prompting around shallow reasoning; a targeted think-first line is the fallback when effort must stay low. A standing "think carefully" line was drafted as its own lint rule on 2026-10-07 and not adopted: R-07's fix shape already removes it, and no fleet file carries one.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (Calibrating effort and thinking depth)
- **Instruction files guide; settings enforce** — R-27.
Source: https://code.claude.com/docs/en/memory (Deploy organization-wide CLAUDE.md)
- **Move procedures out** — R-26 (its quoted sentence is from "When to add"); layering nested `AGENTS.md` files is the same advice.
Source: https://code.claude.com/docs/en/memory (When to add to CLAUDE.md) · https://developers.openai.com/codex/guides/agents-md (Layer project instructions)
- **Combined size cap** — R-14.
Source: https://developers.openai.com/codex/guides/agents-md (How Codex discovers guidance)
- **The other vendor's behaviour summary** — an index of five behaviours, each owned by a rule: initiative (R-21, R-22, R-33), instruction following (R-16, R-17, R-29, R-32), writing style (R-20), delegation (R-28), testing (R-03).
Source: https://developers.openai.com/api/docs/guides/latest-model (GPT-6 Astra behavior) · https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra (GPT-6 Astra behavior)
- **The flagship page mirrors the latest-model page** — its detail sections are the same text as the latest-model sections the rules cite; each one's owner is the rule citing the latest-model section of the same name.
Source: https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra (Initiative and follow-through; Instruction following; Personality and writing style; Subagent delegation; Testing and verification)
- **Reasoning models want goals, not scripts** — R-07. That rule carries one vendor's tag and this page is the other vendor saying the same thing: a candidate to widen R-07's tag, not done here.
Source: https://developers.openai.com/api/docs/guides/prompt-engineering (Prompting reasoning models)
- **Verbosity clamp** — R-20.
Source: https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (3.1 Controlling verbosity and output shape)
- **Scope drift** — R-19, and R-13's design exception.
Source: https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (3.2 Preventing Scope drift (e.g., UX / design in frontend tasks))

## Background appendix — guidance with no instruction-file signal

Recorded so a cold reader has the reasoning; none of these are scanned. Tags name whose guidance each is.

- `[shared]` **Effort is the primary depth control** on current models; prompt prose is the fallback, and it is the weaker lever: "Asking it in the system prompt to think less doesn't reliably reduce its thinking" — lower the effort level instead. Tuning effort per tier is `docs/model-tiers.md`'s job, not this skill's.
- `[anthropic]` **Keep conversation history append-only** and put per-turn reminders in turn-scoped system messages — a harness concern (prompt cache and thinking-block validity), not an instruction-file one.
- `[anthropic]` **Put long-form data at the top of a prompt, the query at the bottom** — queries at the end "can improve response quality by up to 30 percent" on multi-document inputs. Instruction files are not long-context prompts; applies to skills that assemble prompts for the local hub.
- `[shared]` **Sampling parameters** (`temperature`, `top_p`, `top_k`) are rejected by the newest models — an API concern.
- `[anthropic]` **Search a fast-moving name as written when it is unfamiliar** (low effort) — applies to research skills; `/sota-watch` already works that way.
- `[anthropic]` **Gerund skill names** ("Consider using gerund form"). Not adopted as a rule: fleet skills are routed by existing verb/noun names (`issue-add`, `sota-watch`), and renaming breaks every trigger. New skills may use it.
- `[openai]` **Slop-word lists** (phrases to avoid in generated prose) — an output-style concern for user-facing writing, not for instructions.
- `[anthropic]` **Tell the model what output the user can't see** — "If your product collapses or hides tool output, tell the model. Otherwise it may run commands to 'show' the user output that your UI never displays." Delivered as a turn-scoped system message, so a harness concern.
- `[anthropic]` **Writing density / mannered prose** — a section on style calibration for generated prose, not for instructions.
- `[anthropic]` **Mark pasted text** with `<pasted_content>` tags plus a system-prompt note — a harness concern; skills that ingest transcripts (`/issue-add`) may adopt it.
- `[anthropic]` **Parallel-calls instruction as a turn-scoped system message** in long loops — a harness concern.
- `[anthropic]` **HTML block comments are stripped** from `CLAUDE.md` before injection, so they are a token-free place for maintainer notes.
- `[anthropic]` **`CLAUDE.md` shadows `AGENTS.md`**: under the default `claude-md-or-agents-md` setting the agent reads `AGENTS.md` only when no `CLAUDE.md`, `.claude/CLAUDE.md` or local-override instruction file exists in the working directory or above; `~/.claude/CLAUDE.md`, the managed `CLAUDE.md` and `.claude/rules/` do not count, and `claude-md-and-agents-md` loads both. Reading `AGENTS.md` directly needs agent CLI v2.1.277 or later. A fact for the fleet's `AGENTS.md` interop, not a file defect.
- `[anthropic]` **Conditional workflow pattern** — "Guide [the model] through decision points" with a branch per case. Fleet skills already branch by argument and label (`/issue-start`'s mode table); R-38 judges whether a branch is pinned tightly enough.
- `[anthropic]` **Visual analysis** — render inputs as images for the model to read. A technique for skills whose inputs are visual (`/design-review` already screenshots); no instruction-file signal.
- `[anthropic]` **Runtime environment and package availability** — the vendor-hosted app and API code-execution sandboxes (what is preinstalled, no network). The fleet runs its skills locally in the coding-agent CLI, so only the forward-slash consequence (R-36) and the "list your packages" consequence (R-43) apply.
- `[anthropic]` **Next steps** — links to the quickstart and the other skill guides; navigation, not guidance.
- `[shared]` **Effort names aren't comparable across models** — "Effort level names don't correspond to the same amount of thinking across models" (the per-model page, not the hub, now carries it), and a new model's levels are recalibrated: "a level doesn't produce the same amount of thinking as the same level on" its predecessor. The owner is `docs/model-tiers.md`.
- `[anthropic]` **Mid-turn user input and harness notices** — user text goes in a user turn, never inside a `tool_result` block; reminders go in a separate system message. A harness concern.
- `[anthropic]` **JSON output on reasoning tasks** — use structured outputs, add "Think the problem through before you answer.", prefer adaptive thinking over `between_tools`, parse the last JSON value; with thinking off and structured outputs the model can skip a needed tool call. API and harness concerns; skills that call the hub with JSON schemas may adopt them. R-07 bounds the think-first line.
- `[anthropic]` **Tolerant tool-call handling** — accept an unambiguous case-different tool name, or return `is_error: true` with the exact expected name. A harness concern.
- `[anthropic]` **Give the current date next to a search tool** — render it once at conversation start and keep `system` and `tools` identical on later requests. Applies to research skills that assemble prompts for the hub.
- `[anthropic]` **Prompt edits can break resumed conversations** — a request that sends thinking blocks back after `system` changed can return a 400, so suggested text goes into new conversations only. A harness concern.
- `[anthropic]` **Per-message effort change** (beta) — keeps the prompt cache where a top-level `effort` change invalidates it. Owned by `docs/model-tiers.md`.
- `[anthropic]` **Reasoning-like text in user-visible replies** — more common with thinking off or at `low`; switch to adaptive thinking at `medium`. A model-selection fact, not a file defect.
- `[anthropic]` **The vendor's built-in audit** — `/doctor prompt-audit` (agent CLI v2.1.283 or later) flags instructions written for older models, references to files or commands that don't exist, and files that contradict each other. It overlaps R-01 to R-11 and R-16 but has no ledger or fleet-wide scope, so it does not replace this skill. A dead-reference check is the one item with no rule here; deferred, not adopted.

Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Long context prompting; Optimize parallel tool calling) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Keep the conversation history append-only; Search triggering at low effort; Ask for user-facing progress updates; Writing density) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (Calibrate effort; Mark pasted text in user messages) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Calibrate effort; Running without up-front thinking; Reasoning tasks with JSON output; Mid-turn user messages; Tolerant tool-call handling) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 (Accurate search results; Use adaptive thinking with JSON output and your own tools; Mid-turn user messages; Keep reasoning out of user-facing text) · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Naming conventions; Conditional workflow pattern; Use visual analysis; Runtime environment; Package dependencies; Next steps) · https://code.claude.com/docs/en/memory (Import additional files; Audit your instruction files; When Claude Code reads AGENTS.md; Choose which instruction files load) · https://developers.openai.com/api/docs/guides/latest-model (Personality and writing style; Update API and model parameters)

Entries for sections the coverage check found uncited (fleet-config#1307):

- `[anthropic]` **Model-specific navigation** — the hub's per-model table and next-steps links; `docs/model-tiers.md` owns which model a tier means.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Model-specific guidance; Next steps)
- `[anthropic]` **Be clear and direct; give a role; say how far an instruction reaches** — the colleague test ("Show your prompt to a colleague with minimal context on the task and ask them to follow it.") is a reviewer's heuristic with no mechanical signal; R-18 and R-19 judge its parts, and the role technique stays (first rejected entry). A literal reader does not generalise an instruction from one item to the next, so a task prompt names the set it applies to ("every section, not just the first"). Drafted as a judgment rule on 2026-10-07 and not adopted: fleet instruction files state general rules rather than one-item edits, and R-19 owns task scope.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Be clear and direct; Give Claude a role) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (More literal instruction following)
- `[anthropic]` **Product-prompt snippets** — model-identity strings, plain-text math instead of LaTeX, document and slide creation, and a quick summary of the work after tool use. API-product details; length is R-20 and update cadence R-04.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Model self-knowledge; LaTeX output; Document creation; Communication style and verbosity)
- `[anthropic]` **Chain complex prompts** — draft, review and refine across separate calls to inspect intermediate output; a pipeline-design concern, not instruction prose.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Chain complex prompts)
- `[anthropic]` **Scratch-file cleanup and no test-fitting** — optional task-prompt lines: remove temporary files at the end; implement the general solution, not one that only passes the test inputs. The global file's "Implement only what was asked" carries the intent; no detectable signal.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Reduce file creation in agentic coding; Avoid focusing on passing tests and hardcoding)
- `[anthropic]` **Crop, zoom and resolution for dense visual inputs** — give a model that reads dense charts or technical drawings a tool to crop, zoom or run code on the image (a crop-only tool delivers most of the gain); on charts, tools help more than raising effort; send higher-resolution images; re-test scaffolding built for older models. A technique for skills with visual inputs (`/design-review` screenshots); extends the visual-analysis entry above.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Improved vision capabilities) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Give vision work tools to crop and zoom) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (Tools for complex visual inputs) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Tools for complex visual inputs)
- `[shared]` **Effort sweep per model** — start at the default effort and test the others against your own evals; tuning is `docs/model-tiers.md`'s.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Consider all effort levels)
- `[anthropic]` **Compaction summary prompt** — name what a summary must keep (problems, options set aside, exact decisions, current state, open items); a harness prompt, not an instruction-file defect.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Tell the model what to preserve in compaction summaries)
- `[anthropic]` **Safeguard declines** — a classifier decline returns `stop_reason: "refusal"` with a category, and a resend usually returns another refusal, so the client handles it. Asking "Are there any bugs in this program?" rather than whether it compiles, documenting a lesser-known language, and stripping encoded blobs from tool output lower false positives. API and tool-output concerns; the one prompt-side cause, asking for reasoning in the response, is R-30 on the models that carry that category.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Reduce safeguard false positives) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 (Safeguard refusals)
- `[anthropic]` **Targeted edits and long-output headroom** — one system-prompt line to prefer surgical edits over whole-file rewrites; at the two highest effort levels, leave `max_tokens` headroom, because thinking and reply share one limit. Harness settings.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Prefer targeted edits over whole-file rewrites; Leave room for long outputs at xhigh and max effort)
- `[anthropic]` **Non-blocking subagent tools** — a start-subagent tool returns at once, results arrive later, and a separate wait tool exists; a harness design. Skill-side batching is R-23 and the delegation stance R-28.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Let the lead agent keep working while subagents run)
- `[anthropic]` **Capability notes and time budgets** — per-model capability summaries; a harness that appends elapsed and budgeted time lets the model pace itself, but advisory only, so a hard timeout stays.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5 (Capabilities relevant to prompting; Time signals for multiagent harnesses)
- `[anthropic]` **Tone, design defaults, interactive coding and computer use** — re-evaluate style prompts after a model change; a generic anti-style instruction swaps one fixed palette for another, so name a concrete alternative (the fleet's `design.md`) or ask for options first; state task, intent and constraints in the first user turn; computer-use tool versions and image resolution. Product and user-prompt advice.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (Tone and writing style; Design and frontend defaults; Interactive coding products; Computer use)
- `[anthropic]` **Where instruction files live and how they load** — managed, user, project, local and additional-directory scopes; excluding files by pattern; load order. Useful to `/context-audit` altitude checks; no per-file signal.
Source: https://code.claude.com/docs/en/memory (Choose where to put CLAUDE.md files; Set up a project CLAUDE.md; Load from additional directories; Exclude specific CLAUDE.md files)
- `[anthropic]` **Path-scoped rule files** — `paths` is the only frontmatter field read; YAML that doesn't parse loads the rule as if unscoped; a glob with an unreadable `[` matches nothing; one topic per file; a symlinked rule whose target is outside the working directory loads only after approval. No fleet repo has a `.claude/rules/` directory, so the lint drafted on 2026-10-07 would never fire; revisit when one appears.
Source: https://code.claude.com/docs/en/memory (Set up rules; Path-specific rules; Share rules across projects with symlinks)
- `[anthropic]` **Reading `AGENTS.md`** — when support is unavailable, how it differs from the main instruction file, and why it may not load. Extends the shadowing entry above.
Source: https://code.claude.com/docs/en/memory (When AGENTS.md support is unavailable; Where AGENTS.md differs from CLAUDE.md; My AGENTS.md isn't loading)
- `[anthropic]` **Pointing one agent's file at the other's** — a file that tells the reader in words to open the other file is read only if the model decides to, while an import line loads it; the Edit and Write tools refuse to write through a symlink; a committed symlink checks out as a plain text file on Windows unless `core.symlinks` is on. Fleet repos point the other way (a one-line `AGENTS.md` naming `CLAUDE.md`, which `docs/cross-agent-parity.md` records being followed in a live run) and commit no symlink, so the lint drafted on 2026-10-07 would never fire.
Source: https://code.claude.com/docs/en/memory (Remove an earlier AGENTS.md workaround; Share one file with other coding tools)
- `[anthropic]` **Importing other tools' instruction files** — the agent CLI's init and import commands fold other tools' rule and instruction files into the project file as a one-time copy.
Source: https://code.claude.com/docs/en/memory (Migrate instructions from other tools)
- `[anthropic]` **Auto memory mechanics** — a per-repo memory directory shared across worktrees; only the start of its index loads at session start, topic files on demand; `/memory` browses and edits. The index is machine-local and outside the audited files, so no rule scans it.
Source: https://code.claude.com/docs/en/memory (Enable or disable auto memory; Storage location; How it works; Audit and edit your memory; View and edit with `/memory`; I don't know what auto memory saved; Related resources)
- `[openai]` **Model catalogue, API features, limits and migration** — model tier descriptions, async tool calling, mid-turn steering, mid-conversation reasoning changes, data-residency limits, the migration command and the API-parameter checklist. API and product facts; the flagship page repeats them.
Source: https://developers.openai.com/api/docs/guides/latest-model (GPT-6 Astra; GPT-6.1 Sol; What's new; Limitations; Migrate with Codex) · https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra (GPT-6 Astra; GPT-6.1 Sol; What's new; Limitations; Migrate with Codex; Update API and model parameters)
- `[openai]` **Prompt-engineering basics** — choosing a model; pinning snapshots and keeping evals (R-44's counterpart); message roles and precedence; prompts versioned in code; stable content first for caching; context-window sizes; an empty coding heading; next-step and resource links.
Source: https://developers.openai.com/api/docs/guides/prompt-engineering (Choosing a model; Prompt engineering; Message roles and instruction following; Version prompts in code; Save on cost and latency with prompt caching; Planning for the context window; Coding; Next steps; Other resources)
- `[openai]` **Front-end engineering** — recommended libraries and instruction categories for large front-end codebases; fleet web apps follow `design.md`.
Source: https://developers.openai.com/api/docs/guides/prompt-engineering (Front-end engineering)
- `[openai]` **The archived prompting recipe** — introduction, behavioural differences, migration steps and conclusion of a page now marked archived. Its re-grounding advice for long inputs and its compaction advice are prompt-assembly and API concerns; its structured-extraction advice (a schema, required versus optional fields, null rather than a guess) is what R-38 asks of a wire format.
Source: https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (1. Introduction; 2. Key behavioral differences; 3.3 Long-context and recall; 4. Compaction (Extending Effective Context); 7. Structured extraction, PDF, and Office workflows; 8. Prompt Migration Guide to GPT 5.2; 10. Conclusion)
- `[openai]` **Coding-harness wiring** — the starter prompt (its parallelism line is R-23); tool design (patch, shell, plan, image-view, terminal-wrapping and custom tools with specific names; tool-output truncation); the `phase` marker on assistant items; personality snippets; and metaprompting (ask the model which instruction caused a bad turn, sample several times, keep the common elements, measure with an eval, as R-44 does for skills). Harness and tool-definition concerns.
Source: https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide (Recommended Starter Prompt; Apply\_patch; Shell\_command; Update Plan; View\_image; Dedicated terminal-wrapping tools; Other Custom Tools (web search, semantic search, memory, etc.); Tool Response Truncation; Preamble messages; Values; Where it appears; How it’s used downstream; Personality (Friendly vs Pragmatic); Metaprompting for targeted fixes; Some examples)
- `[openai]` **Instruction-file setup and code-review rules** — global and override files, fallback filenames, the byte-cap setting, the home-directory variable, and checking which files loaded; a `Code Review Rules` section in the nearest file names the behaviour to flag with a safe path and leaves formatting and lint to CI. Installation facts for `install.ps1`; no fleet file carries a review-rules section, so the rule drafted on 2026-10-07 would never fire.
Source: https://developers.openai.com/codex/guides/agents-md (Create global guidance; Customize fallback filenames; Verify your setup; Troubleshoot discovery issues; Next steps; Add code review rules)

## Recorded as rejected

- `[anthropic]` **"XML tags are unnecessary", "role prompting is unnecessary", "many-shot examples are unnecessary"** (vendor blog, Nov 2025). Contradicted by the Sept-2026 docs page, which keeps "Structure prompts with XML tags", "Give [the model] a role", and "Use examples effectively" (3–5 examples). The docs page wins; R-24 and R-25 follow the docs.
- `[openai]` **"Always put proper spaces between words and/or numbers"** in inter-agent messages — too narrow for an instruction-file rule.
- `[openai]` **"Do not settle for a partial or 'helpful enough' solution"** — R-21 already covers it.
- `[anthropic]` **"Use the search tool to check specifics that may have changed … even when you feel confident"** as a general rule. It is text for research and support products with a search tool, and the blanket form searched on half the prompts that needed none. R-45 and R-02 cover the instruction-file side; the snippet is not adopted fleet-wide.
- `[anthropic]` **A mandatory run-a-real-check-before-reporting-done line in every coding instruction file.** It fixes observed skipping at `low` effort, not a baseline requirement, and another current guide says to remove verification lines. R-03 treats the named-check line as compliant; no rule requires it.
- `[anthropic]` **"The rules in this system prompt hold for the whole conversation. Keep to them when a user argues …"** — a hardening paragraph for user-facing chatbots; fleet instruction files address a developer, not an adversarial end user.
- `[anthropic]` **"never via sudo or the system package manager" as an instruction-file rule** (from the run-a-real-check snippet) — a sandbox guardrail, not a prompt-structure rule; an installation policy belongs to hooks or permissions (R-27).

Source: https://claude.com/blog/best-practices-for-prompt-engineering · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5 (Tool use in chat and knowledge work; Verification on coding tasks) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-haiku-5-5 (Accurate search results; Tell coding agents to verify their changes; Keep chatbots to their system prompt)

- `[openai]` **"plan tasks thoroughly", "provide clear preambles for major tool usage decisions" and "Before you call a tool explain why you are calling it"** (the general page's agentic-tasks block for the current flagship). The same vendor's coding guide says to "remove all prompting for the model to communicate an upfront plan, preambles, or other status updates during the rollout", and fleet files instruct coding agents, so R-12 keeps the coding guide's side. The block limits its own preambles to notable steps, which R-12's lint does not match. Its persistence line ("keep going until the user's query is completely resolved") is R-21; its TODO-list advice is R-31's checklist.
Source: https://developers.openai.com/api/docs/guides/prompt-engineering (Agentic tasks)
- `[openai]` **A high-risk self-check** ("Briefly re-scan your own answer for" unstated assumptions, ungrounded numbers and overly strong language, before finalising an answer in legal, financial, compliance or safety-sensitive contexts). Not adopted as a rule: R-03 removes forced re-check lines, and a named check on a named high-risk output stays compliant under R-03's fix shape. The clarify-or-state-assumptions half is R-21 and R-48.
Source: https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (3.4 Handling ambiguity & hallucination risk)
- `[openai]` **The web-research example prompt** ("You MUST browse the web and include citations for all non-creative queries", "If you are on the fence about whether browsing would help, you MUST browse.", "Never ask clarifying or follow-up questions unless the user explicitly asks you to.", "Default to being detailed and useful rather than short"). Not adopted: blanket must-use-tool and push-for-length instructions conflict with R-01, R-02 and R-20, and the page is a sample for one web research agent. Its stopping rule is in R-47.
Source: https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide (Example prompt for a web research agent:)
