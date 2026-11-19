# Fleet inventory mode (`/design-review fleet inventory [file]`)

Loaded on demand from the `/design-review` skill (`SKILL.md`, "Fleet inventory mode"), which keeps the argument and the hard rules. This mode captures every web app's surface inventory from its own e2e suite, compares the apps, has a reader settle what the DOM cannot, and files every divergence where its fix belongs.

It runs in three steps. Every command is the `design_review` CLI from step 0 of `SKILL.md`.

## 1. Capture (foreground, serial)

```
... design_review fleet --inventory [--devices iphone,desktop,android]
```

This is fleet mode's loop with every app measured by `measure --e2e`: the app's synthetic walk, then its own e2e suite in surface-capture mode. It runs one app and one browser at a time, and only on each app's throwaway instance. **It never probes, walks, starts or restarts a live app.** An app with no `[design.review.synthetic]` launcher is `unmeasured` (`SYNTHETIC_UNDECLARED`), and the sweep goes on. The budget is the synthetic walk plus the e2e suite per app, so plan on several minutes per app. Run it once, in the foreground, and block on it until it exits. Never fire it and end the turn.

Read the fleet lines from `fleet.md` plus:

- `SWEEP_APP=<app> unmeasured= e2e= tree= surfaces= never_walked= not_walked= screens= classified= unclassified= repeat=`
  - `tree` is what the suite left in the app's checkout. `E2E_TREE_RESTORE_FAILED=<app>` means it is left changed: stop and report it before anything else.
  - `surfaces` is the app's surface-map entry count.
  - `never_walked` is the map entries no screen showed.
  - `not_walked` is the open-overlay count the run never opened (`NOT_WALKED`).
  - `repeat` compares the inventory hash with the app's previous inventory sweep. `identical` or `changed` means the same commit. `commit changed` or `first` means nothing was compared.
  - `unknown` is a state: an app whose count could not be established is never 0.
- `NOT_WALKED_NONZERO=<app:n|unknown,...>|none`: every adopted app should be `none` here.
- `CLASSIFY=<app> entries= answered= PROMPT= ANSWERS=`: one per app with unclassified controls.
- `DIVERGENCES=<app:n,...>`
- `FILING=dry-run`: the capture never files, and `--inventory --file` is refused.

## 2. Classify (one read-only reader per app)

For each `CLASSIFY=` line, spawn one **easy-tier** reader (`docs/model-tiers.md`; Sonnet on Claude Code) with a fresh context. Bind the spawn to this host's tools through workflow-capabilities. The reader's brief is only:

> Read `<PROMPT>` and follow it. You are read-only: open the screenshots it names and write exactly one JSON file at `<ANSWERS>`. Edit nothing else, run nothing but file reads, and do not guess: leave out any entry the screenshot does not settle. Finish within this turn.

The prompt carries each entry's stable id, its masked label, its example screen and that screen's local screenshot, plus the fixed answer vocabulary: the comparator's action names or `none`, or an option count and `select` / `combobox`. Run readers serially or a few at a time. This session is the only hard-tier process, so the cap on concurrent hard-tier sub-agents is untouched. Wait for every reader to finish before step 3. If no reader can be spawned on this host, skip this step and say so: step 3 then reports every entry as still unclassified, never as answered.

## 3. Report (re-compare, then file on approval)

```
... design_review fleet-report <FLEET_DIR> [--file]
```

This step measures nothing. It reloads the captured runs and validates each `classify-<app>.json`. An answer is dropped and printed as `CLASSIFY_PROBLEM=<app> <why>` when it has an unknown id, an answer outside the vocabulary, the wrong field for its entry, a count that is not positive, or a second answer for the same id. A missing answers file leaves that app's entries unclassified. The step then re-runs the comparison with the valid answers and rebuilds `fleet-digest.json` / `.html` and every would-be body in `<FLEET_DIR>`.

The digest's Inventory sweep section has three parts:

- the per-app table;
- the divergences by text role, by action, by bespoke kind and by choice;
- the divergence count per app.

The Inventory section beneath it lists every cross-app divergence and every bespoke control that has a vendored equivalent. Each line names the vendored fix path and `/propagate-vendored`.

The default is a dry run. Publish `fleet-digest.html` as a private artifact where the host has one, else print the path. Then ask the owner to approve it. Only on that approval, re-run with `--file`. That merges each single-app finding and the SURF-01/02 surface-map lines into the app's managed `design-review` issue, and the cross-app findings into fleet-config's. Lines carry ids, counts, renderings and screen ids only. A body never carries a label, a key, a screenshot path or captured page text.

## Summary block

```
/design-review fleet inventory — <stamp> (rubric v<version>)

  apps:        <n> declared · <n> measured · <n> unmeasured (<name:reason, …> | none)
  <app>:       surfaces <n|unknown> · never walked <n|unknown> · NOT_WALKED <n|unknown> · repeat <state> · classified <n> / left <n>
  not walked:  <NOT_WALKED_NONZERO>
  readers:     <n> spawned · <n> answers dropped (<app: n, …> | none) | not run (<why>)
  divergences: <INVENTORY= counts> · per app <DIVERGENCES>
  filed:       <repo#N …> | dry run: <n> bodies at <FLEET_DIR> (re-run fleet-report with --file on approval)
  digest:      <DIGEST_HTML>   <artifact URL | path only>
```
