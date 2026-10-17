# Fleet mode (`/design-review fleet [--devices ...] [file]`)

Loaded on demand from the `/design-review` skill (`SKILL.md`, "Fleet mode"), which keeps the argument, the hard rule "Fleet mode is serial and never restarts" and the ratchet.

One command replaces steps 1–5 and 6b for every declared web app:

```
... design_review fleet [--devices iphone,desktop,android] [--file]
```

It walks every `hooks/projects.toml` table with a `webapp_port` in file
order, **one app at a time** — probe, measure, evaluate, ledger + diff,
render — so never more than one browser is open. An app whose port is
`NOT_LISTENING` or `TIMEOUT` gets an `unmeasured` run and ledger entry and is
**never started, restarted or killed**; the sweep goes on. The judgment stage
is **skipped** (the checklist is a per-app, on-demand step) and the digest
says so. Budget: ~4 minutes and ~144 PNGs per listening app, serially — run it
once, never in a loop, never in a session that cannot stay for the whole
sweep.

Read `FLEET_DIR=`, `APPS=`, one `APP=<name> probe= unmeasured= grade= score=
failed= fixed= regressed= new= run=` line per app, `MEASURED=`,
`UNMEASURED_APPS=<name:reason,...>|none`, `SPEC=`, `SCAFFOLD=`,
`PROMOTED=` (an **app-owned rule failing in two or more apps** is promoted
to the scaffold list once and left off every app issue), `JUDGMENT=skipped`,
`FILING=dry-run|filed`, `ISSUE=<repo> <url | body path>` per repo,
`DIGEST=` / `DIGEST_HTML=` — `fleet-digest.json` + `fleet-digest.html`
under `~/.claude/hooks/state/design-review/_fleet/<stamp>/`, the apps
table, the spec-owned list (fleet-config's issue), the scaffold-owned +
promoted list (project-scaffolding's issue), the filing outcome. Without
`file` every would-be body is written beside the digest and nothing
reaches GitHub; with `file` the app issues and the two digest issues are
upserted through the same merge as step 6b. Publish `fleet-digest.html` as
a private artifact where the host has one, else print the path.

Summary block for a sweep:

```
/design-review fleet summary — <stamp> (rubric v<version>)

  apps:       <n> declared · <n> measured · <n> unmeasured (<name:reason, …> | none)
  <app>:      <grade> <score> · <n> app-owned · <n> promoted · <n> fixed / <n> regressed / <n> new   (per measured app)
  <app>:      unmeasured — <reason>                                                                   (per unmeasured app)
  spec:       <ids | none>   → fleet-config
  scaffold:   <ids | none>   → project-scaffolding   (promoted: <ids | none>)
  judgment:   skipped in fleet mode — run /design-review <app> for the checklist
  filed:      <repo#N …> | dry run: <n> bodies at <FLEET_DIR> (pass `file` to upsert)
  digest:     <DIGEST_HTML>   <artifact URL | path only>
```
