"""Shared, fail-open client for the local LLM hub (127.0.0.1:8000).

Routes a single-turn text completion through the hub's OpenAI-shape
``/v1/chat/completions`` endpoint via stdlib ``urllib`` — zero-install (mirrors
``slack_notify`` and ``.claude/skills/insights-weekly/report.py``), never an inline
``claude -p`` wrapper. The model is the hub's job; this just asks.

Best-effort by contract: any connection error, timeout, non-200, or malformed
body returns ``None`` so hook/automation callers degrade gracefully (skip the
digest, retry next run) instead of failing. Nothing here ever raises.

Config via env so a backend swap needs no code change:
  * ``HUB_URL``        — default ``http://127.0.0.1:8000/v1``
  * ``HUB_CHAT_MODEL`` — default ``claude-haiku-4-5`` (cheap digest tier;
                         point at ``claude-sonnet-4-6`` / a local model id when
                         that backend is loaded)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger("hub_client")

HUB_URL = os.environ.get("HUB_URL", "http://127.0.0.1:8000/v1").rstrip("/")
DEFAULT_MODEL = os.environ.get("HUB_CHAT_MODEL", "claude-haiku-4-5")


def complete(
    prompt: str,
    *,
    model: "str | None" = None,
    max_tokens: int = 400,
    timeout: float = 30.0,
    temperature: float = 0.3,
) -> "str | None":
    """Return the hub's text reply to ``prompt``, or ``None`` on any failure.

    Single-turn only — exactly what digesting/summarising needs. ``model``
    routes host-side (claude ids → the local ``claude -p`` subscription;
    qwen/glm/gemma → llama-server); the caller picks the tier.
    """
    body = json.dumps(
        {
            "model": model or DEFAULT_MODEL,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{HUB_URL}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local-dummy"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, TimeoutError) as exc:
        logger.warning("hub call failed (%s): %s", model or DEFAULT_MODEL, exc)
        return None
    try:
        text = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return None
    return text or None
