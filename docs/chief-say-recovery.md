# `chief_ops say --verify`: recovery from a transport failure

A steer is an unsigned paste into a live session, so a resend can run a shipping command twice. `say --verify` therefore resends only after a transient transport failure, and an exact-text check of the session transcript gates every resend. Plain `say` is one POST and never resends: it prints `SENT`, `FAILED`, or `UNCONFIRMED=` when a timeout or reset means the steer may have landed.

## What is retried

- **Transient** failures: connection refused, host or network unreachable, a session-host `503`, a connection reset or abort, and a timeout. Refused, unreachable and `503` prove nothing was handled; a reset or timeout after the request went out may have landed the write.
- **Never retried**: any other HTTP answer. `502` (written, never echoed), `409` (session gone), `400` and `403` (refused) are authoritative negatives and end as `STRANDED`; a `404` or `500` ends as `UNKNOWN`.

## The bound

At most 3 sends in all, waiting 1 s, 2 s and 5 s after the first, second and third failure (`launcher.RECOVERY_BACKOFF`). Before every further POST the transcript (`GET /api/claude-code/sessions/<sid>/transcript`) is read for the exact steer text as a user turn:

- A copy newer than the pre-send baseline (copies already in the transcript before the first send) ends the call as `DELIVERED` with no further POST. An earlier identical steer is never taken for this one, and an assistant turn quoting the text does not count.
- A readable transcript without the text allows the resend.
- An unreadable transcript (the route says `available: false`, or the read itself fails) allows a resend only while every failure so far was refused, unreachable or `503`. After a timeout or reset it stops.
- After the last attempt a readable transcript with no copy is `PENDING` and an unreadable one is `UNKNOWN`, each with the attempt count. Neither is a delivery, and exchange or assistant-turn movement alone never decides a resend.

The same transcript proof also ends a successful send as `DELIVERED` ahead of the exchange poll.

## The sid lookup

`say` and `stop` resolve an 8-char prefix against `/api/board`. That read takes the same bounded retry (a GET has no side effect), so a launcher that restarts between the chief's call and the lookup is waited out before anything is sent. A read that still fails with a refused connection passes the id through unchanged; a raw timeout or reset still ends as `UNCONFIRMED=`.

The code is `skills/_lib/chief/say_recovery.py` (the transcript evidence and the send loop) and `skills/_lib/chief/launcher.py` (`transient_failure`, `request_with_recovery`, `post_session_input`); the proof is `tests/chief_ops/test_session_commands.py`, driven with injected transport failures.
