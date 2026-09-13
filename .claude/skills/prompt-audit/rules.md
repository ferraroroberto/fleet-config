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
- **File scope** — `any`; `claude-md` = the always-on instruction files (`CLAUDE.md`, `AGENTS.md`, `.claude/rules/*.md`); `skill` = a `SKILL.md`.
- **Tier** — who can fix it unattended. `easy`: a mechanical rewrite (delete a line, soften a word, add a marker). `hard`: restructuring, removing a step list, resolving a contradiction, or any edit to `global-CLAUDE.md` / `project-scaffolding/CLAUDE.md` regardless of the rule's own tier.
- **Detect** — `lint` rules are counted exactly by `audit.py lint` (a hit is a candidate, confirmed or rejected by the judgment pass); `judgment` rules have no mechanical signal and are assessed by the judgment agent alone.
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
Detect: lint — "double-check", "re-verify", "verify your answer/work/output/response/changes", "final verification step", "subagent to verify", "re-check your".
Why: current models verify their own work unprompted; explicit verification instructions "cause over-verification … and removing them reduces wasted tokens with no loss in quality". Self-correction prompts like "double-check your answer" "compound with the model's own behavior and add cost without improving results". The other vendor's flagship guide makes the same point for coding: calibrate testing, because the model "tends to be thorough in testing" and over-tests small changes.
Fix shape: delete the line. If one specific check is genuinely required (a named gate command), name that check once, without "double-check".
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Task scope and over-verification; Self-correction) · https://developers.openai.com/api/docs/guides/latest-model (Testing and verification)

### R-04 No periodic progress-summary scaffolding        tags: [shared] [file: any] [tier: easy]
Detect: lint — "every N tool calls", "every few tool calls", "after every N steps".
Why: "If you've added scaffolding to force interim status messages ('After every 3 tool calls, summarize progress'), try removing it." The coding-model guide agrees: "remove all prompting for the model to communicate an upfront plan, preambles, or other status updates during the rollout, as this can cause the model to stop abruptly before the rollout is complete."
Fix shape: delete; if updates matter to a human reader, say what an update should contain, not how often.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (User-facing progress updates) · https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide (Getting Started)

### R-05 No narration suppression        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "hold (all) findings/results/updates/output for/until the final …".
Why: "Some earlier models were eager to give updates while working, which led to system prompt lines such as 'hold all findings for the final response.' Remove lines like that before adding anything."
Fix shape: delete the line.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Ask for user-facing progress updates)

### R-06 No blanket anti-formatting blocks        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — lines that forbid markdown, bullets, headers, headings, bold, lists or formatting ("do not use markdown", "never use bullet points", "no headers").
Why: "Earlier models overused bullets and bold in chat, and many prompts carry anti-formatting rules written to hold that down." Current models lean the other way: "If your prompt contains anti-formatting language, remove it or replace it with a rule that says when specific formatting is appropriate." Positive framing works better too: instead of "Do not use markdown", say what the output should be.
Fix shape: replace with the positive rule for *when* a format is appropriate; a format rule with a real downstream reason (a renderer, a wire format) stays, with the reason.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Formatting in chat) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Control the format of responses)

### R-07 No hand-written reasoning plans        tags: [anthropic] [file: any] [tier: hard]
Detect: lint — "think step by step", "step-by-step reasoning/thinking", "reason step by step", and numbered list items that open with think / reason / reflect.
Why: "A prompt like 'think thoroughly' often produces better reasoning than a hand-written step-by-step plan. [The model]'s reasoning frequently exceeds what a human would prescribe."
Fix shape: replace the plan with a one-line goal plus "think thoroughly"; keep numbered steps only where they are an operational procedure (commands in order), not a reasoning script.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Leverage thinking & interleaved thinking capabilities)

### R-08 No "do not think" rules        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "do not / don't / never think" or "… reason" (not followed by about / of / that).
Why: "If your system prompt contains a rule instructing the model not to think or not to reason, remove it; that kind of instruction increases tag leakage."
Fix shape: delete; control depth through effort, not prose.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Running with thinking disabled)

### R-09 No recall-suppressing review instructions        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "only report high-/critical-severity", "be conservative", "don't nitpick".
Why: "If your review prompt says 'only report high-severity issues' or 'be conservative,' the model may follow that instruction literally and report less; ask it to report everything and filter in a separate pass instead."
Fix shape: ask for every finding with a severity/confidence label; filter in a separate step.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Capability improvements) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (Code review harnesses)

### R-10 No prefill or thinking-budget references        tags: [anthropic] [file: any] [tier: easy]
Detect: lint — "prefill" (any form), "budget_tokens".
Why: prefilled last assistant turns "are no longer supported" and return a 400 error on current models; `budget_tokens` "is deprecated" and returns a 400 on newer models — "move budget control to `effort`".
Fix shape: delete the instruction or replace with the effort-based equivalent.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Migrating away from prefilled responses; Leverage thinking)

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
Fix shape: rewrite the rule as the behaviour wanted, keeping a negative only where it names a real hazard (a destructive command); never mass-rewrite — judgment decides per line.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Control the format of responses) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5 (Response length and verbosity)

### R-14 Instruction-file size cap        tags: [shared] [file: any] [tier: hard]
Detect: lint — `CLAUDE.md` / `.claude/rules/*.md` over 200 lines; `SKILL.md` body over 500 lines; `AGENTS.md` over 32768 bytes.
Why: "target under 200 lines per CLAUDE.md file. Longer files consume more context and reduce adherence." "Keep SKILL.md body under 500 lines for optimal performance." The `AGENTS.md` loader "stops adding files once the combined size reaches the limit defined by `project_doc_max_bytes` (32 KiB by default)" — past the cap, instructions are silently truncated.
Fix shape: move procedures and scoped detail into skills, path-scoped rules or referenced docs (R-26); never compress by deleting directives — `/context-purge` owns lossless compression.
Source: https://code.claude.com/docs/en/memory (Write effective instructions) · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Token budgets) · https://developers.openai.com/codex/guides/agents-md

### R-15 Skill description: short, third person        tags: [anthropic] [file: skill] [tier: easy]
Detect: lint — description over 1024 characters; first- or second-person words (I, me, my, we, our, you, your) in the description prose, with quoted trigger phrases excluded. Prose word count is reported against the fleet's 50-word cap (owned by `/context-audit`).
Why: the description "is injected into the system prompt, and inconsistent point-of-view can cause discovery problems" — "Always write in third person." The field has a 1024-character maximum.
Fix shape: rewrite the prose in third person; leave quoted trigger phrases verbatim (they are user utterances and the routing surface).
Source: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Writing effective descriptions)

### R-16 No contradictory rules        tags: [shared] [file: any] [tier: hard]
Detect: lint — within one file, the same action phrase under both an always/must and a never/do-not modal ("always commit to X" vs "never commit to X"). Judgment adds contradictions the pattern cannot see, including across the global file and a project file.
Why: "if two rules contradict each other, [the model] may pick one arbitrarily. Review your CLAUDE.md files … periodically to remove outdated or conflicting instructions." The other vendor: "unclear or conflicting guidance in a skill file may cause the model to pause and block work early."
Fix shape: resolve to one rule with its scope stated ("in a worktree …"); record which one won in the owning issue's decision log.
Source: https://code.claude.com/docs/en/memory (Write effective instructions) · https://developers.openai.com/api/docs/guides/latest-model (Instruction following)

### R-17 Vendor-specific guidance carries a neutrality marker        tags: [shared] [file: any] [tier: easy]
Detect: lint — in an agent-neutral file or section, a paragraph that names one audience's product terms (`sources.toml` `terms`) and none of the other's, without any audience marker.
Why: a neutral file is read by several agents; guidance tuned for one model is noise or harm for another. Both vendors stress auditing instruction files for guidance that does not apply to the reader, and the per-model guides carry their own hedge: a technique measured on one model is re-checked before being applied to another.
Fix shape: add the audience marker listed in `sources.toml` to the heading, or move the paragraph to the vendor-scoped file. A paragraph that merely names a product as data (a model id in a routing table) is compliant.
Source: https://developers.openai.com/api/docs/guides/latest-model (Instruction following) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Model-specific guidance)

### R-18 Give the reason behind the rule        tags: [anthropic] [file: any] [tier: hard]
Detect: judgment — a directive whose motivation is neither stated nor linked (an issue number or a one-clause "because" counts).
Why: "Providing context or motivation behind your instructions, such as explaining to [the model] why such behavior is important, can help [the model] better understand your goals and deliver more targeted responses." A reasoned rule generalises to cases the rule's wording missed.
Fix shape: add one clause of why, or the issue link that holds it.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Add context to improve performance)

### R-19 Explicit scope boundary        tags: [shared] [file: any] [tier: hard]
Detect: judgment — a task-shaped instruction (a skill, a workflow section) with no statement of what is in and out of scope.
Why: current models "can also expand the scope of a task, adding steps that weren't requested"; "Deliver what was asked, at the scope intended." The other vendor: "infer the user's intent and task scope from the instructions and prior conversation context."
Fix shape: one sentence naming the deliverable and what is explicitly out of scope.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Task scope and over-verification) · https://developers.openai.com/api/docs/guides/latest-model (Initiative and follow-through)

### R-20 Explicit length calibration        tags: [shared] [file: any] [tier: hard]
Detect: judgment — an instruction that produces a written artefact (report, issue body, digest, summary) with no length or density guidance.
Why: effort controls how much the model thinks, not how long it writes; written deliverables "are often longer than on prior models … add explicit length calibration". The other vendor: the model "tends toward detailed, formatted responses … Specify the writing style and structure your application needs."
Fix shape: one line of length/density calibration for that artefact.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5 (Response length and verbosity; Written deliverable length) · https://developers.openai.com/api/docs/guides/latest-model (Personality and writing style)

### R-21 Autonomy with an assessment carve-out        tags: [shared] [file: any] [tier: hard]
Detect: judgment — an unattended or long-running workflow that does not say to carry reversible work through without asking, or that has such a block without the "a question is answered with an assessment, not a fix" exception.
Why: the autonomy block — "The user is not watching in real time … For reversible actions that follow from the original request, proceed without asking" — is paired with "when the user is describing a problem, asking a question, or thinking out loud … the deliverable is your assessment." The other vendor: "bias towards action and carry the user's intended task to completion" unless "clearly destructive or irreversible".
Fix shape: add the missing half; keep the opening sentence as written.
Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Finish the whole task) · https://developers.openai.com/api/docs/guides/latest-model (Initiative and follow-through)

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
Why: "XML tags help [the model] parse complex prompts unambiguously, especially when your prompt mixes instructions, context, examples, and variable inputs." For instruction files: "use markdown headers and bullets to group related instructions" — "organized sections are easier to follow than dense paragraphs".
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
Why: instruction files are "context rather than enforced configuration" — "there's no guarantee of strict compliance". "If the instruction is something that must run at a specific point … write it as a hook instead." A guarantee that nothing enforces teaches readers to trust it.
Fix shape: name the enforcing hook/check, or reword as guidance and file the enforcement gap.
Source: https://code.claude.com/docs/en/memory · https://developers.openai.com/codex/guides/agents-md

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

## Background appendix — guidance with no instruction-file signal

Recorded so a cold reader has the reasoning; none of these are scanned. Tags name whose guidance each is.

- `[shared]` **Effort is the primary depth control** on current models; prompt prose is the fallback. Tuning effort per tier is `docs/model-tiers.md`'s job, not this skill's.
- `[anthropic]` **Keep conversation history append-only** and put per-turn reminders in turn-scoped system messages — a harness concern (prompt cache and thinking-block validity), not an instruction-file one.
- `[anthropic]` **Put long-form data at the top of a prompt, the query at the bottom** — queries at the end "can improve response quality by up to 30 percent" on multi-document inputs. Instruction files are not long-context prompts; applies to skills that assemble prompts for the local hub.
- `[shared]` **Sampling parameters** (`temperature`, `top_p`, `top_k`) are rejected by the newest models — an API concern.
- `[anthropic]` **Search a fast-moving name as written when it is unfamiliar** (low effort) — applies to research skills; `/sota-watch` already works that way.
- `[anthropic]` **Gerund skill names** ("Consider using gerund form"). Not adopted as a rule: fleet skills are routed by existing verb/noun names (`issue-add`, `sota-watch`), and renaming breaks every trigger. New skills may use it.
- `[openai]` **Slop-word lists** (phrases to avoid in generated prose) — an output-style concern for user-facing writing, not for instructions.

Source: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices (Long context prompting) · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1 (Keep the conversation history append-only; Search triggering at low effort) · https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices (Naming conventions) · https://developers.openai.com/api/docs/guides/latest-model (Personality and writing style; Update API and model parameters)

## Recorded as rejected

- `[anthropic]` **"XML tags are unnecessary", "role prompting is unnecessary", "many-shot examples are unnecessary"** (vendor blog, Nov 2025). Contradicted by the Sept-2026 docs page, which keeps "Structure prompts with XML tags", "Give [the model] a role", and "Use examples effectively" (3–5 examples). The docs page wins; R-24 and R-25 follow the docs.

Source: https://claude.com/blog/best-practices-for-prompt-engineering · https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
