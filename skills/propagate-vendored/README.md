# propagate-vendored — design notes

Companion to `SKILL.md` (the runnable flow). This file carries the decision
writeup the issue asked for — schema choice and why — so `SKILL.md` can stay
terse and imperative.

## Manifest schema: `.fleet.toml [vendored]`, not a separate `VENDORED.lock`

**Decision: `.fleet.toml`'s `[vendored]` table.** Full schema:
`architecture/README.md`'s "Optional per-repo `[vendored]` table" section.

**The concrete conflict check the issue asked for, run and confirmed clean:**
`.claude/skills/system-map/build_data.py`'s `card_from_toml(repo_name, meta)`
is the one place in the fleet that parses a `.fleet.toml` with `tomllib` and
turns it into map data. It reads exactly six named keys off the parsed dict
via `meta.get(...)` / `meta[...]` (`layer`, `icon`, `description`,
`display_name`, `port`, `chips`, `tag`) and never iterates `meta`'s full key
set. Verified directly:

```python
import tomllib, sys
sys.path.insert(0, ".claude/skills/system-map")
import build_data
meta = tomllib.loads('layer = "working-web"\nicon = "x"\ndescription = "d"\n\n[vendored]\nnav = { src = "a", sha = "abc", dest = "a" }\n')
build_data.card_from_toml("testrepo", meta)
# -> ('web', {'ic': 'x', 'nm': 'testrepo', 'ds': 'd'})  — identical to the no-[vendored] case
```

An unrecognized top-level table costs nothing on the map-build path: no
`KeyError`, no drift-test failure (`tests/run_acceptance.py`'s `fleet_toml`
checks assert `fleet.data.js` matches `build_data.py`'s output and that every
`.fleet.toml` is *valid* TOML — neither checks for an exhaustive key set). So
the one concrete objection the issue raised against `.fleet.toml` — "unless
you find a concrete conflict with system-map's `build_data.py` tomllib
parsing" — doesn't hold. No `build_data.py` change was needed.

**Why `.fleet.toml` over `VENDORED.lock` on the merits, independent of that
check:**

- **One file per repo, already the map-card location.** Every repo in the
  `_adopted` registry already carries a `.fleet.toml`; a `[vendored]` table
  there is one more table in a file that already exists and is already read
  by tooling, versus a second file with its own existence/staleness
  guarantees to invent from scratch.
- **Same anti-staleness contract, no new one to write.**
  `architecture/README.md` already states "update `.fleet.toml` in the same
  PR as any material change" for the map-card fields; extending that sentence
  to cover `[vendored]` is free. A `VENDORED.lock` would need its own
  "who writes this, when" rule, plus a second entry in `tests/run_acceptance.py`'s
  drift assertions to keep it honest.
- **Precedent in this repo:** `config.residual.json` and `fleet.residual.json`
  already coexist with `.fleet.toml` as *hand-maintained, non-derivable*
  metadata — but `[vendored]` is the opposite shape: it's *derived from a
  propagation run*, not hand-maintained, so it belongs with the other
  machine-written per-repo fields (map card) rather than with the residuals.

**What would have flipped the answer to `VENDORED.lock`:** if `card_from_toml`
had iterated `meta`'s full key set (e.g. to reject unknown keys, or to embed
the whole parsed table in the map data), a `[vendored]` table would have
broken every adopter's map card the moment this skill wrote one. It doesn't,
so it doesn't. If a future refactor of `build_data.py` *does* start doing
that, this is the section to revisit — regenerate the drift check above
first, don't assume the old answer still holds.

## Component granularity: one `[vendored]` entry per copied path

A directory-shaped UI component (`nav`, `card`, `disclosure`, …) gets one
entry keyed by its component name, `src`/`dest` pointing at the directory. A
single-file machine-local primitive (`app/tray/tray_lifecycle.ps1`,
`app/tray/single_instance.py`, `tray.bat.template`) gets its own entry keyed
by a stable short name (`tray_lifecycle`, `single_instance`, `tray_bat_template`),
`src`/`dest` pointing at the one file. `skills/_lib/vendored_drift.py`'s
`hash_dir_local` / `hash_dir_at_ref` both handle a file or a directory
transparently (a file hashes to `{basename: sha256}`), so the manifest schema
and the drift helper never need to know which shape a given component is.

## Why this skill never files a per-repo issue

The scaffold issue (or the scaffold's own fix PR) is the single record of
*why* the change is correct — a byte-for-byte re-vendor carries zero new
decisions, so an issue per adopter would just be N copies of "see upstream."
`audit_issue.py`'s managed-issue machinery (used by `/codebase-audit` and
`/design-sync`) exists for exactly the opposite case — recurring findings
that need a living checklist — and is deliberately **not** reused here. The
generated PR body itself carries the link back to the scaffold change (with
"Part of …" phrasing, never `Closes` — GitHub's closing-keyword parser
matches substrings, so a literal `Closes`/`Fixes` in a distribution PR could
accidentally close the scaffold issue on the first adopter to merge).

## Why the manifest alone cannot report coverage (project-scaffolding#230)

The adopter list is built from adopters' own `[vendored]` entries. That is
self-referential: it can only find the repos that already told us. A repo
carrying the component with no entry is not "skipped" in any visible sense — it
is *absent from the question*, so the wave re-vendors whoever declared the
component, prints a green digest, and leaves the rest on stale bytes with
nobody told. `#228` prescribed `/propagate-vendored tailscale_cert` for seven
adopters; only `task-os` had declared it, so the wave would have covered one
repo and reported success. The stale copies were the ones holding a real tailnet
hostname in public repos, and the gap was caught only because someone grepped
the adopters directly instead of trusting the manifest.

The missing half is a catalog of what the scaffold *publishes*:
`project-scaffolding`'s own `.fleet.toml` `[components]` table (schema:
`architecture/README.md`). With it, `vendored_drift.py scan` can hash every
fleet repo's copy of a known component path and report `undeclared_carriers`
alongside `adopters`, and this skill states both counts in every report.

Two rules follow, and both are about not overclaiming:

- **Detection reports; it never overwrites.** A carrier byte-identical to the
  scaffold tip (`matches_head: true`) simply never recorded what it copied, and
  adopting it is safe. One that differs is either stale or a deliberate fork,
  and those are indistinguishable from the bytes — so it is named, with its
  differing files, and left for a human.
- **Unknown is its own state.** A repo whose `.fleet.toml` will not parse lands
  in `carriers_unknown`, and a scaffold checkout with no `[components]` table
  sets `catalog_known: false`. Neither may be folded into the clean count:
  "found nothing" and "could not look" are different claims, and the second one
  wearing the first one's clothes is the whole defect.
