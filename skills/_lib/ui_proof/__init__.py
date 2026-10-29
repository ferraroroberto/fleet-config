"""Visual proof for UI-touching diffs: record, gate, and a private walkthrough.

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

  walkthrough <repo> --pr <N> --sha <merge sha> [--title ...] [--summary ...]
      One self-contained HTML (inline CSS, `data:` images): each criterion
      with its screenshots and lint result, plus the merge SHA. Stored
      machine-locally keyed `<repo>#<PR>` (never in a tree) and copied under
      `%TEMP%/claude` for `send_file.py`. Never posted to GitHub.

Directory execution: `python <...>/skills/_lib/ui_proof check .`.
"""
