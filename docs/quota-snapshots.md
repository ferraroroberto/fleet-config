# Shared quota snapshots

Native quota measurements have a shared consumer contract in skills/_lib/quota_snapshot.py. They are separate from model discovery, request/token telemetry and the Codex terminal footer. A model being listed never establishes quota or permission to run it.

## Producers and refresh

- Claude: statusline-command.ps1 sends its already-built quota subset to quota_sources.py after writing the existing rate-limits.json. This uses the original native render's captured_at, never the reader's time. The legacy five_hour/seven_day fields, numeric reset epochs, BOM-less JSON and existing consumer behavior stay unchanged.
- Codex: quota_sources.py codex makes one bounded native app-server account/rateLimits/read request using the installed CLI's normal authentication. It initializes the local stdio child and checks account/read with refreshToken=false first. It never starts a thread or model turn, invokes an account mutation, reads auth files, or copies credentials. Only its own disposable child is stopped.
- Pi/Grok: no native producer is claimed here. Adapters can publish an empty_source with their actual provider and unsupported/unknown state using the same schema. Harness is a route, never the provider or an independent quota allowance.

Claude refreshes on existing native statusline renders; Codex refresh is explicitly on demand. There is no new daemon, scheduled task, hook, provider-default change or automatic polling loop. A future consumer can invoke the one-shot collector when refresh is requested; serialize/rate-limit repeated requests at that consumer. The legacy cache is not repurposed as the Codex producer.

From the primary checkout, refresh Codex, then read all sources:

~~~powershell
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/quota_sources.py codex
& E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/quota_sources.py read
~~~

Both commands accept --state-dir PATH. Otherwise CLAUDE_HOOKS_STATE_DIR is honored, falling back to ~/.claude/hooks/state. The publish command prints only producer/state/reason, with no account IDs or quota values. The read command prints the local consumer JSON, including native measurements and opaque keys; don't paste it into public logs/issues. Scripts can import read_snapshot(directory, now=aware_datetime) directly. The producer exits 1 for an error or failed write; unsupported/unknown are valid recorded outcomes, not proof of available allowance.

For a local migration/proof, pipe an existing Claude rate-limits.json into quota_sources.py claude --state-dir PATH. Its captured_at must be preserved; copying old measurements never makes them fresh. Normal operation passes the subset directly from the native statusline, not by rereading the cache.

## Contract version 1

Storage is quota-v1/<producer>.json, one atomic document per producer. The reader returns schema_version, read_at, sources and pools. It always includes explicit unknown records for absent Claude/Codex producers. Additional adapter files are discovered without a schema change. Invalid JSON, unreadable paths, unknown schema/adapter versions, contradictory available states and provider/producer mismatches produce an error source with no measurements.

Each source carries:

| Field | Meaning |
| --- | --- |
| producer / harness / provider | Stable producer ID, actual originating harness, underlying provider; known native mappings cannot be relabelled. |
| state / reason | Measurement status and categorical non-secret reason. No raw upstream error strings. |
| checked_at | UTC time this collection attempt ran, distinct from when a measurement was observed. |
| source | kind, adapter_version, nullable numeric client_version. Provenance stays with each selected pool. |
| observations | Native bucket measurements; empty for absent, unsupported or failed sources. |

Each observation carries bucket, account, pool_id, observed_at, expires_at, state and windows. Every window carries its native id, duration_minutes, nullable used_percentage, nullable resets_at, and state. Resets and all other timestamps are normalized to timezone-bearing UTC strings ending in Z. Epoch seconds are accepted at the adapter boundary; naive timestamps, booleans, nonfinite/out-of-range percentages and nonpositive durations are not accepted as available measurements. Null reset times remain null; they do not become the current time or an estimated reset.

Codex primary and secondary retain their native labels and duration values. A primary window can be weekly; it is never assumed to mean five hours. Null/absent windows are omitted, not generated with zero usage. Missing utilization on a reported window is null/unknown. Claude's explicitly named five_hour/seven_day windows carry their defined 300/10080-minute durations. Multiple Codex limit buckets remain separate, and the single-bucket compatibility view is not duplicated when that bucket is in rateLimitsByLimitId. Credits, plan/upsell data, earned reset credits, individual spend controls, token activity and catalogs are outside this measurement contract; a percentage does not promise an account can execute a request.

## Consumer rules

1. Evaluate freshness on every read through read_snapshot. A stored available value is an observation, not a freshness guarantee: the reader marks measurements stale at ten minutes or when their known reset has passed. It never predicts zero usage after a reset. Future observation/attempt clocks are unknown, never fresh. A failed refresh replaces that source with an explicit error rather than silently retaining an available response.
2. Available means a valid, fresh measurement, including at 100% utilization. It does not mean remaining capacity. Check source, observation and window states before using a percentage. A source can contain several buckets with different states. Unknown fields do not become zero; stale values may be displayed as historical evidence only.
3. Account identity and measurement availability are distinct facts. account.key is a one-way provider-namespaced SHA-256 of a native account ID; raw IDs, email, credentials, plan data and transcripts are not stored. Claude's statusline exposes no account identity, so its account is unknown and pool_id is null even when its native measurement is available. Missing Codex accountId gets the same treatment. Never assign a shared default identity or infer identity from an email, model, harness name or credential hash.
4. Identified observations share a pool_id only for the same provider/account/bucket. The pools list chooses the latest observation for that pool and records contributing harnesses; it never adds percentages. Thus Pi and Codex verified against the same underlying native account/bucket do not create two pools. Account changes produce a different opaque key. Unbound observations remain in sources only and cannot be aggregated as independent pools.
5. Even distinct identified buckets may overlap. Never sum their percentages or translate them into a model allowance. The contract does not assert a universal per-model bucket mapping.
6. Future adapters must use their actual provider and confirmed native account scope, or leave identity unknown. Unverified Pi/Grok routes cannot adopt another harness's pool merely because the configurations look similar. For account-switching/native-auth races, identity must arrive in the same native quota response, not a separate account lookup.

States are available (fresh valid evidence), stale (expired evidence), unknown (not established), unsupported (tested absent or unverified source version/auth mode), and error (a read/parse/process failure). A file-write failure retains the previous complete file, which continues aging from its original observed_at; the CLI reports failure. Consumers must not treat a write acknowledgement or fresh checked_at as proof of a fresh measurement.

## Atomicity and concurrency

Each producer owns one file. There is no shared read-modify-write document, so Claude, Codex and future adapters cannot overwrite one another's records. A producer writes and fsyncs a unique same-directory temporary file, then atomically replaces its own target. Windows sharing/access failures are retried at 10/20/40/80/160 ms, then reported as errors. Other failures are not retried. Failed attempts clean their own temporary file and preserve the previous complete target; a process killed mid-write may leave an ignored temporary file, never a readable partial JSON document.

Concurrent same-producer writers are last-completed-write wins. Readers use the embedded observation clock, not file modification time, so a delayed older writer cannot renew freshness. This is a latest observation cache, not history or an event stream. Individual shards are atomic; sources are sampled independently, not as one cross-provider transaction. Native Windows replacement can briefly make an open fail; the reader represents that as error instead of fabricating an available window.

## Source provenance and verification limits

The supported Codex source is the official [Codex App Server account interface](https://learn.chatgpt.com/docs/app-server#auth-endpoints), with account/rateLimits/read and its primary/secondary duration, utilization and epoch reset fields. The response's optional multi-bucket view is supported. No rollout/transcript parser and no undocumented account HTTP endpoint are used. accountId is an optional field observed in the installed native response; missing identity safely stays unknown.

App-server is experimental, so the collector currently admits only the locally probed CLI 0.153.3. Other versions publish unsupported/client_version_unverified without spawning app-server. Adding a version requires repeating the native read-only probe, inspecting its response shape, and updating the sanitized fixtures/version set. Unknown adapter or on-disk schema versions are rejected. Claude's native statusline quota subset is unversioned; its shape is validated field by field rather than asserting an unavailable CLI version. Verified native producer versions do not certify future versions.

Local verification used native Codex account reads without a model turn and matched the Claude native /usage display to the actual fresh legacy cache, with reset display times converted from UTC to the host's real timezone. The PowerShell integration test drives the real statusline into disposable state and compares both shared windows to the unchanged legacy fields. Sanitized tests cover malformed/missing fields, source/schema drift, different and shared accounts, UTC/future/stale clocks, nullable windows/resets, unsupported adapters, unreadable files, failed replacements and concurrent processes/readers. Raw account values and native transcripts remain local and are not fixtures or publication artifacts.

The app-launcher UI/consumer migration is separate (app-launcher#847); current readers continue using rate-limits.json. The Codex footer remains independently configured by codex_statusline.py (#752).
