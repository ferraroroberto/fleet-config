// config.data.js — the data behind the fleet config & convention map. GENERATED
// by .claude/skills/config-map/build_data.py — DO NOT hand-edit. Edit each source
// it derives from (install.ps1, skills/, hooks/, settings.template.json,
// codex-hooks.json, each repo's .claude/skills) or the hand-maintained
// architecture/config.residual.json, then regenerate:
//   py .claude/skills/config-map/build_data.py
// Loaded as plain JS (works under file://, no CORS): sets window.CONFIG.
// The body is strict JSON so Python (build_data.py + the drift test in
// tests/run_acceptance.py) can read it too: strip "window.CONFIG =" + trailing ";".
// Contains only wiring/structure — never a secret value from settings.json.
window.CONFIG = {
  "agents": [
    {
      "key": "claude",
      "nm": "Claude Code",
      "ic": "🟧"
    },
    {
      "key": "codex",
      "nm": "Codex",
      "ic": "🔷"
    },
    {
      "key": "pi",
      "nm": "Pi",
      "ic": "🥧"
    },
    {
      "key": "copilot",
      "nm": "Copilot",
      "ic": "🐙"
    },
    {
      "key": "agy",
      "nm": "Antigravity",
      "ic": "🌌"
    }
  ],
  "matrix": [
    {
      "cls": "Global instructions",
      "sub": "the always-on context file",
      "cells": {
        "claude": "~/.claude/CLAUDE.md",
        "codex": "~/.codex/AGENTS.md",
        "pi": "~/.pi/agent/AGENTS.md",
        "copilot": "~/.copilot/copilot-instructions.md",
        "agy": "— (IDE, no home file)"
      }
    },
    {
      "cls": "Design system",
      "sub": "design.md + design.dark.md",
      "cells": {
        "claude": "~/.claude/design.md",
        "codex": "via ~/.claude",
        "pi": "via ~/.claude",
        "copilot": "via ~/.claude",
        "agy": "—"
      }
    },
    {
      "cls": "Skills",
      "sub": "auto-scanned SKILL.md dir",
      "cells": {
        "claude": "~/.claude/skills",
        "codex": "~/.agents/skills",
        "pi": "~/.agents/skills",
        "copilot": "~/.copilot/skills",
        "agy": "❌ plugin-only (#160)"
      }
    },
    {
      "cls": "Commands / prompts",
      "sub": "slash-command bodies",
      "cells": {
        "claude": "~/.claude/commands",
        "codex": "~/.codex/prompts",
        "pi": "—",
        "copilot": "—",
        "agy": "—"
      }
    },
    {
      "cls": "Hooks",
      "sub": "lifecycle code + wiring",
      "cells": {
        "claude": "~/.claude/hooks",
        "codex": "~/.codex/hooks",
        "pi": "❌ no hook surface",
        "copilot": "❌ no hook surface",
        "agy": "❌ (IDE)"
      }
    },
    {
      "cls": "Statusline",
      "sub": "session footer",
      "cells": {
        "claude": "statusline-command.ps1",
        "codex": "native TUI footer (config.toml)",
        "pi": "extensions/statusline.ts",
        "copilot": "❌",
        "agy": "❌"
      }
    },
    {
      "cls": "Settings / permissions",
      "sub": "tool config",
      "cells": {
        "claude": "settings.json (holds secrets)",
        "codex": "config.toml",
        "pi": "settings.json (tool-managed)",
        "copilot": "config.json (tool-managed)",
        "agy": "VS Code settings"
      }
    }
  ],
  "skills_universal": [
    {
      "nm": "codebase-audit",
      "ds": "Audit a codebase's resting state against its CLAUDE.md and senior-dev maintainability standards — duplication, stale/dead code, c…",
      "scope": "repo"
    },
    {
      "nm": "design-sync",
      "ds": "Check a web app's CSS custom properties (light + dark) against the fleet design system (~/.claude/design.md + design.dark.md), re…",
      "scope": "repo"
    },
    {
      "nm": "handoff-commit",
      "ds": "Generate a copy-paste markdown prompt that hands off a specific GitHub commit to another LLM, instructing it to apply the same lo…",
      "scope": "repo"
    },
    {
      "nm": "issue-add",
      "ds": "Turn a rough idea, brain-dump, or transcript into a well-formed, self-contained GitHub issue — researches the codebase for contex…",
      "scope": "repo"
    },
    {
      "nm": "issue-batch",
      "ds": "Fan out a batch of GitHub issues to parallel background sub-agents — one agent per issue, with git worktrees when multiple issues…",
      "scope": "fleet"
    },
    {
      "nm": "issue-finish",
      "ds": "Finish a GitHub issue cleanly — confirm acceptance, update docs/README, run the verification gate, push, open a PR that closes th…",
      "scope": "repo"
    },
    {
      "nm": "issue-finish-batch",
      "ds": "Ship a set of already-built, already-reviewed issue branches in parallel — fan out one background Sonnet agent per branch, each r…",
      "scope": "fleet"
    },
    {
      "nm": "issue-start",
      "ds": "Start work on a GitHub issue — pick the issue, sync the main branch, cut a feature branch, load project context, then either pres…",
      "scope": "repo"
    },
    {
      "nm": "issue-triage",
      "ds": "Pull every open GitHub issue across all repos owned by ferraroroberto, score each one Small/Medium/Large by reading title + body,…",
      "scope": "fleet"
    },
    {
      "nm": "issue-yolo",
      "ds": "One-shot the full GitHub-issue workflow end-to-end — file the issue, cut the branch, build, validate hard, then ship (PR, CI, mer…",
      "scope": "repo"
    },
    {
      "nm": "screen",
      "ds": "Attach recent screenshots from E:\\downloads\\snaps to the conversation without manual copy-paste.",
      "scope": "repo"
    }
  ],
  "skills_fleet": [
    {
      "nm": "audit-fleet",
      "ds": "Run /codebase-audit across every repo in the E:\\automation fleet in one pass and emit one weekly digest (GitHub comment + Slack p…",
      "sched": true
    },
    {
      "nm": "cleanup-fleet",
      "ds": "Take one bucket of audit findings (a label like documentation, drift or bug) and fan out one background agent per repo to fix eve…",
      "sched": false
    },
    {
      "nm": "config-map",
      "ds": "Regenerate the fleet config &amp; convention map (introspect install.ps1 + the skill/hook dirs + a per-repo git sweep, render to arch…",
      "sched": true
    },
    {
      "nm": "context-audit",
      "ds": "Audit the fleet's always-on context surface — CLAUDE.md token budgets, skill-description word counts, and single-home-by-altitude…",
      "sched": true
    },
    {
      "nm": "insights-weekly",
      "ds": "Diff Claude Code's newest /insights report against the previous one (via the local LLM hub) into a concise weekly \"what changed\"…",
      "sched": true
    },
    {
      "nm": "learning-log",
      "ds": "Weekly learning log + forward horizon + productivity stats distilled from the fleet's GitHub work stream (merged PRs and closed i…",
      "sched": true
    },
    {
      "nm": "system-map",
      "ds": "Regenerate the fleet architecture map (crawl every repo under E:\\automation, render to architecture/system-map.png) and post the…",
      "sched": true
    }
  ],
  "skills_repo": [
    {
      "repo": "content-management",
      "items": [
        "schedule-autoheal"
      ]
    },
    {
      "repo": "life-os",
      "items": [
        "_recap",
        "_shared",
        "_template",
        "alt-text",
        "geek-out",
        "ip-check",
        "is-this-ai",
        "journal-daily",
        "journal-weekly",
        "meeting-prep",
        "roast-posts",
        "sparring-private",
        "sparring-work",
        "visual-muse"
      ]
    }
  ],
  "hooks": [
    {
      "nm": "pre_commit_no_ai_trailer",
      "ev": "PreToolUse · Bash",
      "block": true,
      "reach": "Claude + Codex",
      "ds": "Block `git commit` with an AI attribution trailer."
    },
    {
      "nm": "secret_scan_guard",
      "ev": "PreToolUse · Bash",
      "block": true,
      "reach": "Claude + Codex",
      "ds": "Block `git commit` when a live secret is about to be committed."
    },
    {
      "nm": "gh_body_file_guard",
      "ev": "PreToolUse · Bash",
      "block": false,
      "reach": "Claude only",
      "ds": "Nudge away from the two cross-shell payload traps that mangle GitHub bodies."
    },
    {
      "nm": "safe_kill_guard",
      "ev": "PreToolUse · Bash·PowerShell",
      "block": true,
      "reach": "Claude + Codex",
      "ds": "Block dangerous kill / push / commit-bypass patterns."
    },
    {
      "nm": "venv_discipline",
      "ev": "PreToolUse · Bash·PowerShell",
      "block": true,
      "reach": "Claude + Codex",
      "ds": "Enforce the project's `.venv` discipline."
    },
    {
      "nm": "context_filter_hook",
      "ev": "PreToolUse · Bash·PowerShell",
      "block": false,
      "reach": "Claude + Codex",
      "ds": "PreToolUse adapter for the local fleet context filter."
    },
    {
      "nm": "docs_dated_filename_guard",
      "ev": "PreToolUse · Write",
      "block": true,
      "reach": "Claude only",
      "ds": "Block dated retrospective filenames under a `docs/` directory."
    },
    {
      "nm": "py_syntax_check",
      "ev": "PostToolUse · Edit·Write·MultiEdit",
      "block": true,
      "reach": "Claude + Codex",
      "ds": "Surface Python syntax errors immediately after an Edit/Write."
    },
    {
      "nm": "hub_bypass_warn",
      "ev": "PostToolUse · Edit·Write·MultiEdit",
      "block": false,
      "reach": "Claude only",
      "ds": "Nudge away from re-implementing the local LLM hub with an inline `claude -p`."
    },
    {
      "nm": "browser_stealth_lint",
      "ev": "PostToolUse · Edit·Write·MultiEdit",
      "block": false,
      "reach": "Claude only",
      "ds": "Nudge when a browser-launch file is missing the anti-bot stealth kwargs."
    },
    {
      "nm": "notify_on_idle",
      "ev": "Notification",
      "block": false,
      "reach": "Claude only",
      "ds": "Ping Slack when a live session needs attention — so you can stop babysitting."
    },
    {
      "nm": "restart_and_verify_webapp",
      "ev": "/restart-webapp (slash command)",
      "block": false,
      "reach": "Claude only",
      "ds": "Project-aware: kill the webapp PID, bring the webapp back, verify the new build."
    },
    {
      "nm": "conversation_capture",
      "ev": "Stop (opt-in per project)",
      "block": false,
      "reach": "Claude only",
      "ds": "Stop hook — capture a finished Claude Code session as a markdown file."
    },
    {
      "nm": "session_index",
      "ev": "SessionStart (opt-in per project)",
      "block": false,
      "reach": "Claude only",
      "ds": "SessionStart hook — lazily index settled conversation captures."
    }
  ],
  "hooks_helpers": [
    {
      "nm": "_lib",
      "ds": "Shared helpers for the fleet-config hooks."
    },
    {
      "nm": "context_filter",
      "ds": "Local command-output compression for the fleet hook layer."
    },
    {
      "nm": "context_filter_cli",
      "ds": "CLI entrypoint for the fleet context filter."
    },
    {
      "nm": "conversation_index",
      "ds": "Tier-1 conversation index — a cheap, searchable layer over raw captures."
    },
    {
      "nm": "hub_client",
      "ds": "Shared, fail-open client for the local LLM hub (127.0.0.1:8000)."
    },
    {
      "nm": "notify_complete",
      "ds": "Deterministic skill-completion Slack ping — one canonical format per skill."
    },
    {
      "nm": "pi_usage_stats",
      "ds": "Pi coding-agent usage collector."
    },
    {
      "nm": "slack_notify",
      "ds": "Fleet-wide Slack notifier — fire a real, bot-identity Slack notification."
    },
    {
      "nm": "work_summary",
      "ds": "Deterministic work-summary for a merged PR — file/LOC shape, no LLM."
    }
  ],
  "conventions": [
    {
      "ic": "📜",
      "nm": "global-CLAUDE.md",
      "reach": "all 4 agents",
      "ds": "Working method, conventions, git/PR pipeline, fleet doctrine &amp; gotchas — one file symlinked into every agent home."
    },
    {
      "ic": "🎨",
      "nm": "design.md + design.dark.md",
      "reach": "Claude (others via ~/.claude)",
      "ds": "Canonical visual identity + the floating bottom-tab nav contract for every web app."
    },
    {
      "ic": "🏔️",
      "nm": "single-home by altitude",
      "reach": "rule",
      "ds": "Universal → global-CLAUDE.md · shape-specific → project-scaffolding · instance → the repo's own CLAUDE.md. Enforced weekly by /context-audit."
    },
    {
      "ic": "📐",
      "nm": "project-scaffolding",
      "reach": "governance master",
      "ds": "The canonical scaffold every sister repo derives its CLAUDE.md from — conventions flow up, never diverge."
    }
  ],
  "coverage": {
    "total": 28,
    "claude_md": "28/28",
    "fleet_toml": "28/28"
  },
  "principles": [
    [
      "One source",
      "edit in fleet-config, live in every agent home via junctions"
    ],
    [
      "Derive, don't declare",
      "the map introspects install.ps1 + the dirs — it can't go stale"
    ],
    [
      "Describe vs enforce",
      "config-map is the weekly photo · /context-audit flags the drift"
    ],
    [
      "Degrade per agent",
      "hooks, statusline &amp; design bottom out at each agent's surface"
    ]
  ]
};
