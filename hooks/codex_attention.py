"""Notify Telegram when Codex is genuinely waiting for user input.

``PermissionRequest`` is authoritative.  ``Stop`` is advisory: its bounded
final-message excerpt is classified through the local hub and alerts only for
a strict, high-confidence ``awaiting_input`` result.  Every failure is
fail-open and logged as ``not confirmed``.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Optional

import _lib
import hub_client
import notify_on_idle
import notify_send

logger = logging.getLogger("codex_attention")

CLASSIFIER_MODEL = "agentic_light"
CLASSIFIER_TIMEOUT_S = 3.0
CONFIDENCE_THRESHOLD = 0.90
MAX_EXCERPT_WORDS = 70
MAX_CONTEXT_CHARS = 120
DEDUP_FILENAME = "codex-attention-dedup.json"
_MAX_DEDUP_KEYS = 500
_VERDICTS = {"awaiting_input", "finished", "uncertain"}
BELL = "\U0001f514"

_CLASSIFIER_PROMPT = """Classify whether this Codex final response explicitly leaves a user-facing question or decision unanswered. Return only strict JSON with exactly this schema: {\"verdict\":\"awaiting_input|finished|uncertain\",\"confidence\":0.0,\"request\":\"short description or empty string\"}. Use awaiting_input only when the response is genuinely asking the user to answer or choose; commands, reports, acknowledgements, and completed work are finished. For awaiting_input, describe the unanswered request in a short phrase grounded only in the supplied final response. Otherwise use an empty request. If ambiguous, use uncertain.\n\nFinal response:\n"""

_PERMISSION_CONTEXT = {
    "apply_patch": "approval to edit files",
}


def bounded_excerpt(message: object) -> str:
    """Return at most the first 70 whitespace-delimited words."""
    if not isinstance(message, str):
        return ""
    return " ".join(message.split()[:MAX_EXCERPT_WORDS])


def normalize_context(value: object) -> Optional[str]:
    """Return safe single-line notification context, or None when unusable."""
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    redacted = _lib.SECRET_RE.sub("[REDACTED_SECRET]", normalized)
    if len(redacted) <= MAX_CONTEXT_CHARS:
        return redacted
    return redacted[:MAX_CONTEXT_CHARS - 3].rstrip() + "..."


def classify_stop(message: object) -> tuple[str, float, Optional[str]]:
    """Return a strict verdict plus independently validated request context."""
    excerpt = bounded_excerpt(message)
    if not excerpt:
        logger.info("Codex Stop classification not confirmed: final message missing")
        return "uncertain", 0.0, None
    raw = hub_client.complete(
        _CLASSIFIER_PROMPT + excerpt,
        model=CLASSIFIER_MODEL,
        max_tokens=100,
        timeout=CLASSIFIER_TIMEOUT_S,
        temperature=0.0,
    )
    if raw is None:
        logger.info("Codex Stop classification not confirmed: local hub unavailable or timed out")
        return "uncertain", 0.0, None
    try:
        parsed = json.loads(raw)
        if (not isinstance(parsed, dict)
                or not {"verdict", "confidence"}.issubset(parsed)
                or not set(parsed).issubset({"verdict", "confidence", "request"})):
            raise ValueError("unexpected schema")
        verdict = parsed["verdict"]
        confidence = parsed["confidence"]
        if verdict not in _VERDICTS or isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("invalid values")
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence out of range")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.info("Codex Stop classification not confirmed: malformed classifier result (%s)", exc)
        return "uncertain", 0.0, None
    raw_request = parsed.get("request")
    request = normalize_context(raw_request)
    if "request" in parsed and request is None and raw_request is not None and raw_request != "":
        logger.info("Codex Stop request context not confirmed: malformed request field")
    logger.info("Codex Stop classification: %s confidence=%.2f", verdict, confidence)
    return verdict, confidence, request


def permission_context(payload: dict[str, Any]) -> Optional[str]:
    """Describe a native request only from probe-verified structured fields."""
    tool_name = payload.get("tool_name")
    return _PERMISSION_CONTEXT.get(tool_name) if isinstance(tool_name, str) else None


def _dedup_file() -> Path:
    return _lib.state_dir() / DEDUP_FILENAME


def _event_key(payload: dict[str, Any]) -> Optional[str]:
    session_id = payload.get("session_id")
    turn_id = payload.get("turn_id")
    if not isinstance(session_id, str) or not session_id or not isinstance(turn_id, str) or not turn_id:
        return None
    return f"{session_id}:{turn_id}"


def _read_dedup(path: Path) -> list[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _record_once(payload: dict[str, Any]) -> bool:
    """Record a delivered turn; return False when it was already recorded."""
    key = _event_key(payload)
    if key is None:
        logger.info("Codex attention delivery not confirmed: missing session_id or turn_id")
        return False
    path = _dedup_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = _read_dedup(path)
    if key in keys:
        return False
    keys = [*keys[-(_MAX_DEDUP_KEYS - 1):], key]
    temporary: Optional[str] = None
    try:
        fd, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(keys, handle)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        logger.info("Codex attention delivery not confirmed: dedup write failed (%s)", exc)
        return False
    return True


def handle(payload: dict[str, Any]) -> bool:
    """Classify, deduplicate, and deliver one Codex attention notification."""
    event = payload.get("hook_event_name")
    if event not in {"PermissionRequest", "Stop"}:
        return False
    chat, project = _lib.resolve_notify_target(_lib.cwd(payload), category="attention")
    if not chat:
        return False
    key = _event_key(payload)
    if key is None:
        logger.info("Codex attention delivery not confirmed: missing session_id or turn_id")
        return False
    if key in _read_dedup(_dedup_file()):
        return False
    context = permission_context(payload) if event == "PermissionRequest" else None
    if event == "Stop":
        verdict, confidence, context = classify_stop(payload.get("last_assistant_message"))
        if verdict != "awaiting_input" or confidence < CONFIDENCE_THRESHOLD:
            return False
    message = f"{BELL} {project} Codex awaits your input"
    if context:
        message += f"\nWaiting for: {context}"
    board = notify_on_idle.board_link(payload)
    if board:
        message += f"\n{board}"
    delivered = notify_send.notify(message, chat=str(chat))
    if not delivered:
        logger.info("Codex attention delivery not confirmed: Telegram send failed")
        return False
    if not _record_once(payload):
        logger.info("Codex attention delivery not confirmed: dedup state unavailable")
        return False
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        handle(_lib.read_stdin_json())
    except Exception as exc:  # noqa: BLE001 - lifecycle notification must fail open
        logger.info("Codex attention delivery not confirmed: %s", exc)
    _lib.allow()


if __name__ == "__main__":
    main()
