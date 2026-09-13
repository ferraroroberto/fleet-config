# Tailnet-cert conformance (design-sync step 1b)

Loaded on demand from the `/design-sync` skill (`SKILL.md`, step 1b), which keeps the `detect` command and when to run it. Step numbers refer to that file.

It prints `CERT_DRIFT=yes|no`, `REASON=...`, and the `TAILNET` / `SELF_SIGNED` /
`TS_CERT` evidence (`file:line`, or `-`). The verdict is a fixed truth table over
three signals — real tailnet-exposure evidence (a `*.ts.net` URL, a `tailscale
serve`/`funnel` invocation, or a tailnet CGNAT IP) in `README.md`/`CLAUDE.md`/`docs/**`
(a bare prose mention of the word "tailscale" alone does NOT qualify —
fleet-config#418), a `gen_ssl_cert.py`-style provisioner or `/install-ca` route,
and the absence of a `gen_tailscale_cert.py`-style provisioner — so it is
deterministic, not a judgment call. A repo that has triaged and disproved a
finding can declare a durable `[cert]` opt-out in its own `.fleet.toml`
(`architecture/README.md`); that always reports `CERT_DRIFT=no`, so a
closed-as-not-planned issue doesn't get refiled next sweep. `CERT_DRIFT=no` →
note "cert: ok" for the final report and move on.

On `CERT_DRIFT=yes`, file a **separate** deduped `cert-drift` issue (never folded
into `design-drift`). This is the **canonical `audit_issue.py` upsert procedure**
for this skill — step 5's `design-drift` issue reuses the same four-step shape
below with only its own kind/label/title/temp-file-prefix and (richer) body-merge
rule swapped in; don't restate the mechanics there if you're changing them here.

1. **Ensure the label** (idempotent):

   ```
   gh label create cert-drift --color 'b60205' --description 'Tailnet PWA still on self-signed CA instead of the tailscale-cert standard' || true
   ```

2. **Fetch the existing issue:**

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py get --repo <OWNER/REPO> --kind cert-drift
   ```

3. **Build the body.** Fresh → use the template below (fill the evidence from the
   `detect` output). Existing → preserve every ticked `- [x]`, append a dated bullet
   to `## Run log`, never tick/close anything, never add `Closes #`.

4. **Upsert** (creates / edits / collapses strays, stamps the marker):

   ```
   E:/automation/fleet-config/.venv/Scripts/python.exe C:/Users/rober/.claude/skills/_lib/audit_issue.py upsert \
     --repo <OWNER/REPO> --kind cert-drift --label cert-drift \
     --title "audit: cert-drift findings" --body-file <tmpfile>
   ```

   Use a repo-scoped, unique temp file: `E:/tmp/cert-drift-<owner>-<repo>-<short-sha>.md`
   (`<owner>-<repo>` = `OWNER/REPO` with the slash → hyphen). Never a fixed shared name.

**Body shape** for a fresh issue (no hard-wrapped paragraphs — the global CLAUDE.md
rendered-markdown rule applies; the helper prepends the marker):

```markdown
Surfaced by `/design-sync` (tailnet-cert conformance), kept up to date across runs.

## Finding

- [ ] This app is reached over Tailscale and still provisions HTTPS only via a self-signed CA + `/install-ca` mobileconfig trust dance. Migrate to `tailscale cert` (real Let's Encrypt) per the fleet standard. Fix: adopt a `scripts/gen_tailscale_cert.py` (`--check` auto-renew) + webapp wire-up, dropping the self-signed dance.

## Evidence

- tailnet signal: `<file:line>`
- self-signed provisioner: `<file:line | file>`
- tailscale-cert provisioner: absent

## Standard

Canonical decision record: `ferraroroberto/project-scaffolding#89`. Reference impl: `ferraroroberto/grocery-shopping-automation` — `scripts/gen_tailscale_cert.py` (`--check` auto-renew) + `webapp.bat` wire-up.

## Run log

- <YYYY-MM-DD> @ <short-sha>: initial.
```

Title is **stable** — `audit: cert-drift findings`, no count suffix.
