# Context is assembled, not accumulated

Anything loaded into every session, or every invocation of a skill, has to earn its tokens. Detail is read at the point of need. This is the fleet convention behind `/context-audit`'s always-on budget and its bootstrap-loads table (fleet-config#218, #1014). The audit standard itself stays in the skill body. This page is the durable reference for what the audit measures.

## The four rules

1. **Always-loaded files are a thin map.** A `CLAUDE.md`, a skill description, or a bootstrap's identity file says what exists and where to find it. It points at detail and doesn't carry it: `docs/recurring-gotchas.md` behind one-line rules in `global-CLAUDE.md`, and a skill body behind its description.
2. **Directory-specific traps load with the directory.** A trap that only matters inside one folder goes in that folder's own `CLAUDE.md`, or in a path-scoped rule. Claude Code loads a nested `CLAUDE.md` below the working directory when it reads files in that folder, not at launch. A `.claude/rules/*.md` file with `paths:` frontmatter (a list of globs) loads when Claude reads a matching file. A rule without `paths:` loads at launch like `CLAUDE.md` (code.claude.com/docs/en/memory.md, "How CLAUDE.md files load" and "Path-specific rules").
3. **A bootstrap's load list has a budget and stays under the read cap.** A skill that loads files on every invocation keeps each file under the size a single Read returns whole. Past that, Read returns a partial view, so the tail never loads while the step reports success. Claude Code doesn't document that limit as a constant (code.claude.com/docs/en/tools.md); the audit uses 25k estimated tokens, the threshold observed when #1014 was filed. A generated file that grows without bound, like a conversation index, needs a cap in its generator, not a promise to trim it.
4. **No credential-bearing file is auto-loaded.** Passwords, passphrases and tokens live in a file read only at the point of need, never in one every session of a skill loads. The moment any session runs through the local hub onto a non-Anthropic endpoint, everything auto-loaded goes with it.

## What the audit measures

`/context-audit`'s `bootstrap_loads.py` reads a repo's declaration in `hooks/projects.toml`:

```toml
bootstrap_shared   = ["identity/who-i-am.md"]           # repo-relative, loaded by every skill
bootstrap_skill    = ["{skill}/conversations/index.md"] # relative to skills_dir
bootstrap_sections = ["Step 1"]                         # SKILL.md headings whose <x-root>/path refs load
```

It reports each file as `ok`, `over-cap`, `missing` (declared but absent, usually optional) or `unmeasured` (present but unreadable), plus credential-shaped lines by file and count. It never prints a file's contents, so private repos are measured by size and pattern count only. A repo with no declaration isn't measured for bootstrap loads.
