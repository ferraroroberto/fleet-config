# What's NOT a finding

Loaded on demand from the `/codebase-audit` skill (`SKILL.md`, "What's NOT a finding"). "Hard rules" and step numbers refer to that file.

Concrete anti-examples. If a candidate finding looks like a **no**,
**drop it** — don't try to find a way to make it count:

- **Duplication.** No: three lines copied once between two files; a constant
  repeated in two places (local clarity beats premature abstraction). **Yes:**
  a 50-line block copied four times; two parallel implementations of the same
  workflow under different names.
- **Stale / dead code.** No: one slightly outdated comment, a six-month-old
  `TODO`, an unused import (a linter catches the import; the comment doesn't
  materially mislead). **Yes:** an entire orphaned module no caller references;
  a removed feature's scaffolding still imported on startup; a
  `# removed in v2` block shipped in v5.
- **CLAUDE.md drift.** No: a typo in a rule's prose, one instance of slightly
  inconsistent phrasing (the rule still reads correctly). **Yes:** a rule
  violated systematically (CLAUDE.md says "use `.venv`" and three modules use
  `venv/`); a hard rule contradicted by actual shipped behavior.
- **Maintainability.** No: a function name that could be slightly more
  descriptive, a 30-line function that could be 25, a *what* comment on already
  obvious code. **Yes:** a 1500-line god module mixing four unrelated concerns;
  a public API whose identifiers actively mislead about what they return;
  copy-pasted error handling 12 times in one file.
- **Bugs.** No: "this *might* race under high concurrency" without a concrete
  scenario; a bug in code already superseded by other in-flight work; one you
  can't point to a *currently reachable* call path for from a real entry point
  — reachability from something that actually runs today is required, not just
  "the line looks wrong." **Yes:** "this will mis-handle empty input because
  line N reads `xs[0]` with no guard" — name the input, the line, the failure.
- **Documentation.** No: a slightly stale README sentence, a flag described in
  fractionally outdated wording, a missing entry for a trivial internal or
  dev-only helper, a single outdated example a reader would self-correct in
  context. **Yes:** a whole README section documenting a removed subsystem; a
  headline user-facing command/feature absent from the docs entirely; the same
  setup steps duplicated across `README` and a `docs/` file that now disagree
  on the port; a dated `docs/2026-…-retrospective.md` the project's own
  doc-lifecycle rule forbids — name the file/section and the rule or missing
  feature. Bucket 6 is reserved for headline surfaces a new user/dev would
  actually go looking for and not find.
- **Slop.** No: a function a few lines longer than strictly necessary, one
  extra helper, a single defensive `if` for an unlikely-but-possible input
  (local clarity and honest guarding beat golf). **Yes:** a 40-line hand-rolled
  reimplementation of a stdlib one-liner; an entire configurable abstraction
  (strategy class, plugin registry, options dict) with exactly one hard-coded
  caller and no second use in sight; three parallel error-handling arms for
  exceptions the call can't raise; a generated-looking wall of boilerplate that
  collapses to a fraction of the lines — name the span and the line count it
  would shed. (If the fix is *reorganize* rather than *delete*, it's
  maintainability, not slop.)
- **Security.** No: "this input *could* be unsafe somewhere" with no reachable
  sink — the bug bar applies, name the exploitable path. **Yes:** a
  user-controlled value flowing unsanitized into a shell/SQL/eval sink; a
  secret or credential committed in source; a missing authz check on a
  state-changing route; `pickle.loads`/`yaml.load` on untrusted bytes — and it
  takes the step-8b self-heal path, never a public checklist item.

The pattern across all seven: **scale and impact matter**. One-off cosmetic
blemishes are not findings. Systematic problems, structural rot, or concrete
failure modes are.
