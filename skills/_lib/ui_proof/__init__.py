"""Visual proof for UI-touching diffs: record, gate, and the per-repo `.proof/<PR>` page.

A UI change ships only with proof the agent looked at its own work: each UI
acceptance criterion maps to a named state that project-scaffolding's
`tests/e2e/_layout_lint.py` `capture_states` captured at phone and desktop,
light and dark, with the layout lint clean. That helper writes `manifest.json`
outside any checkout; this package binds the manifest to the tree it proves
and answers the finish gate's question.

  record <repo> --manifest <path> --criterion "<state>=<criterion>" ...
      Bind the manifest (its sha256) and the criterion->state map to the
      current tree hash. The record lives at `rev-parse --git-path
      ui-proof.json`, per worktree and outside the tree, like gate evidence.

  check <repo> [--base <ref>]
      `ux_surface` decides whether proof is needed at all. Prints
      `VISUAL_PROOF=n/a|fresh|missing|stale|failing|unknown`, `ENFORCED=` and
      a `NEED=` line naming what is missing. Exit 1 only when enforced and not
      fresh: enforcement switches on in a repo whose `.fleet.toml` `[vendored]`
      lists `layout_lint`, since before that no repo can produce the proof.

  check <repo> --pr <N>
      Also refuses, vendored or not, when `.proof/<PR>/index.html` is absent
      or `.proof/` is tracked or not gitignored (`PROOF_PAGE=`, `NEED=`). A
      built page is itself the proof: with no recorded manifest it reads
      `fresh`, and the lint findings its captures carry (or "not run") are
      the only lint result.

  capture <repo> --phase before|after --url <url> --state <name> [--viewport phone|desktop|<W>x<H>|<label>=<W>x<H>]...
          [--theme light|dark]... [--click <selector>]... [--wait-ms N]
      Drive a running app instance (the repo's gate instance) through the
      repo's own `.venv` Playwright and stage one screenshot per viewport x
      theme (default phone 390x844 and desktop 1280x900, light and dark),
      so no lane writes its own capture test. `--click` reaches the state.

  capture-before|capture-after <repo> [--manifest <path>] [--image "<state>:<viewport>-<theme>=<png>"]
      Copy one phase's screenshots (before on main, after on the branch) into
      `<this checkout>/.proof/_branch/<branch>/`; a manifest also carries
      its layout-lint findings.

  build <repo> --pr <N> --criterion "<state>=<what changed, where to look>" [--no-visual "<criterion>"]
        [--title ...] [--issue N] [--sha <merge sha>]
      Move the branch's captures into `.proof/<PR>/`, write its self-contained
      `index.html` (inline CSS, `data:` images, no scripts; before and after
      side by side per state, one or more criterion lines each, lint result
      or "not run") and the `.proof/index.json` row; copy it under
      `%TEMP%/claude` for `send_file.py`. Re-run with `--sha` after the merge
      to stamp it and copy the folder into the primary checkout's `.proof/`
      (`PUBLISHED=`); before that a worktree lane writes only inside its own
      tree. Never posted to GitHub.

  prune <repo> [--older-than 30d]
      Remove `.proof/` PR and staging folders older than the threshold.

Directory execution: `python <...>/skills/_lib/ui_proof check .`.
"""
