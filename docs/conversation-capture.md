# Shared conversation capture

`hooks/transcript_readers.py` is the stored-transcript boundary for `conversation_capture.py`. Hook envelope adaptation stays in `_lib.normalize_payload`; transcript formats are separate native protocols. Capture is deterministic and does not call a model. `conversation_index.py` still digests settled captures through `hub_client` and the local hub; curated memory remains an explicitly approved downstream operation.

## Opt in

Existing `capture = true` projects keep Claude capture only. Codex additionally requires an explicit harness allowlist in that project's registry entry:

```toml
[example]
cwd_prefix = "E:/automation/example"
capture = true
capture_harnesses = ["claude", "codex"]
capture_routing = "flat"
conversations_dir = "conversations"
```

The shipped Codex `Stop` hook invokes the shared capture entry point. It is inert for unregistered projects, `capture = false`, or harnesses outside the allowlist. No project registry entry is opted into Codex by this change. Life OS adoption and launcher UI are separate issues. Claude's existing project hook wiring remains valid. Installed hook changes become live only when this branch is shipped to the primary checkout; native hook trust still follows the harness's normal approval flow.

Before opting in, keep the selected capture directories, generated `index.md`/`index.json` and `.search.db` in the adopting project's ignored private storage. For flat routing, ignore `conversations/`; for skill routing, ignore each skill's `conversations/` and the archive. This engine never stages, publishes, approves curated memory, installs hooks or alters another project's capture preference. A disposable proof can override only its process's `CLAUDE_HOOKS_PROJECTS_TOML` and `CLAUDE_HOOKS_STATE_DIR` to temporary locations.

## Readers and identity

| Source | Supported interpretation | Identity and lineage |
| --- | --- | --- |
| Claude stored JSONL | `user`/`assistant` records, `message.content` text; native append order. Existing Claude command-tag/preamble handling and skill inference remain. | Exact `sessionId`, checked against hook `session_id`. Conflicting session IDs fail rather than inventing a resume. A native resume retaining its ID updates the same capture. Different IDs are separate, even with identical opening text; absent cross-session lineage is unknown. |
| Codex stored rollout JSONL | `session_meta` plus `event_msg.item_completed` containing `UserMessage`/`AgentMessage`, as observed on CLI 0.153.3. Duplicate completed item IDs are scoped by thread and turn. Separately supported older fixtures use `user_message`/`agent_message`. | Exact `session_meta.id`, checked against hook `session_id`; `forked_from_id` becomes `parent_sid`. Native resume keeps its ID; a fork keeps its own capture and points to its parent. The observed fork rollout contains only its new turns: the reader does not invent or fetch inherited history. |
| Codex `exec --json` stdout | Unsupported as a transcript. This stream lacks stored conversation context/identity metadata. | A `thread.started` event is not a stored rollout. `--ephemeral` prevents persistence and is unsuitable for this proof. |
| Pi / Grok / unknown formats | Unsupported until their own readers and native conformance evidence exist. | Never inferred from Claude-shaped hook envelopes or the first prompt. |

Codex `response_item` records mirror conversational events and also contain injected project/global instructions labelled as user input. They are deliberately excluded, as are tool output, reasoning and token accounting. Mixed old/new Codex event generations currently report unsupported, preserving the previous capture rather than silently discarding one generation. SQLite-only/paginated history without a readable rollout path is not supported. Missing source, unsupported format and parse failure are distinct logged outcomes; all leave prior captures intact. Malformed JSON (including a truncated tail), invalid UTF-8 and inconsistent identity prevent writes. A complete-lines source that shrinks below a stored capture's turn count also preserves the existing capture.

New headers extend the existing `<!-- capture sid="…" agent="…" updated="…" -->` grammar with `schema="2"`, a full harness/session key, turn count, content digest, reader format/version, and optional `parent_sid`. Missing fields remain missing; a missing native ID never gets a fabricated resume command. The source path can provide local idempotence for an identified harness with no native ID, but is not recorded as a resume identity. Search only produces commands for an explicit supported harness and safe native ID.

Updates match full harness + native ID across configured routing folders. They use a temporary file plus atomic replacement, and unchanged captures retain their bytes and mtime. Legacy full-ID headers can be updated in place; headerless files and unrelated sessions with matching opening text or matching last-eight ID characters remain untouched. Old prompt fingerprints and filename helpers remain available for existing consumers, but never authorize replacement or deletion. If an older Claude resume minted a new ID without explicit lineage, both captures remain: recovering lineage by prompt similarity would conflate unrelated sessions.

`index.md`, `index.json` and SQLite search remain derivatives. Existing captures retain the established header/body grammar and remain readable. Rebuild with `conversation_search.py --project <name> --rebuild`; it reads originals and only rewrites the search database. Capture identity takes precedence over stale digest metadata. Fork provenance remains in the original header for future consumers. Index turn counts recognize both Claude and Codex labels; filenames' new full identity hashes stay out of displayed slugs.

## Future reader contract

A reader returns `Transcript(status, harness, session_id, parent_session_id, source_format, source_version, messages, entries, detail)`. `messages` is chronological `(user|assistant, text)` from that source's actual conversation, with replay/stream mirrors deduplicated only using native identifiers. `entries` is source-specific routing evidence, never an assertion that another harness shares its schema. `status` is `ok`, `unavailable`, `unsupported`, or `parse_failure`; errors must not become an empty successful capture. `ok` with no native ID is readable but not resumable. Unrecorded lineage stays unknown.

A Pi/Grok reader must add sanitized native fixtures and demonstrate: exact host/version/store schema; authoritative native identity versus hook hints; root/resume/fork lineage; multiple ordered turns; tool/context exclusion; idempotent replay; unrelated equal prompts; malformed/truncated input; private opt-in routing; and non-destructive index rebuilds. Add a native resume mapping only after its syntax and identity are verified on that harness. Do not parse Pi/Grok by reusing Claude's reader because the hook envelope happens to look similar.

## Verification and native evidence

Run `tests/test_transcript_capture.py`, `tests/test_conversation_search.py`, and the full acceptance gate. Four regressions were observed against pre-change code: Codex captured no turns, same-prompt Claude sessions overwrote one another, missing agent yielded a Claude resume command, and unidentified input acquired a Claude label. The replacement tests exercise native-session isolation, fork/resume updates, parsing failures, atomic write failure, routing, ignored private storage in the native replay, and rebuild preservation. Claude fixtures are synthetic; the Codex fixtures are sanitized projections of the actual disposable saved rollouts.

On 2026-09-05, installed Codex CLI 0.153.3 completed three harmless, tool-free subscription turns: new session `01a07363-fdd1-7561-a7da-3681f2d06cdd` → `CAPTURE753_ALPHA 42`; resume of the same ID → `CAPTURE753_BETA 63`; fork `01a07369-a5be-7380-826b-4fc268b35cd0` → `CAPTURE753_FORK 84`, with the original ID in `forked_from_id`. All native processes exited 0. The original rollout began at 21:05:28 UTC (23:05:28 local, observed +02:00); stored JSONL timestamps are UTC. CLI help confirmed resume/fork and persistence flags; the actual saved JSONL established the store schema.

Each invocation used process-only `exec --ignore-user-config --approve-for-me --disable hooks -c project_doc_max_bytes=0 -c check_for_update_on_startup=false --skip-git-repo-check -C <temporary-project> --json`, with `resume <id>` or `fork <id>` as applicable. No `--ephemeral`, credential copying, API-key fallback, profile ACL changes, production rewiring, live-service changes or external notifications were used.

`tests/probe_conversation_capture.py --rollout <exact-disposable-rollout> --session-id <native-id>` then replayed the saved source through the real capture entry point and temporary opt-in registry. It confirmed ordered turns (4 original/resumed, 2 fork), exact identity/parent, unchanged repeat capture, real CLI search/rebuild, gitignored capture storage, unchanged originals, and no unrelated-project capture. It preserves `evidence.json` and `search.json` under its printed temporary root. The probe accepts only short `CAPTURE753` marker conversations and never calls a model.

Evidence limits are explicit: native model execution, saved-store reading, capture replay and CLI search are verified; automatic native Codex Stop dispatch of the new hook was not exercised, because the disposable native run disabled installed hooks. The replay suppresses only detached digest scheduling to avoid additional model calls and uncollected children. Digest/index semantics are covered by offline tests through the existing hub boundary; this proof does not certify native hook trust, future store formats, a live digest backend, or cross-harness resume.
