"""Stop hook — capture a finished life-os skill session as a markdown file.

Fires when a Claude Code session ends (``Stop`` event). Reads the JSONL
transcript, determines which life-os skill was active, and writes a
``YYYY-MM-DD-HHMM-<slug>.md`` file into that skill's ``conversations/`` dir.

Routing:
  1. Read ``.active-skill`` (written by the skill's Step 0 via the Write tool).
  2. If absent or empty, infer the skill from ``<command-name>`` tags in the
     transcript.
  3. If still undetermined, route to ``conversations/_archive/`` so nothing
     is ever dropped.

Output file format:
  - First line: one-line human description extracted from the first
    substantive user turn.
  - Blank line, then a fenced markdown block of all user/assistant turns
    in order (verbatim — no summarising).

One file per session: the ``Stop`` hook fires at every turn-end (not once per
session), so a single session triggers several captures — e.g. a cold-start
readiness-ack turn, then the real work. Each run derives a stable token from
the session id, removes any earlier capture of the same session, and writes the
latest, fullest transcript — collapsing the session to a single file. Without a
session id we can't identify siblings, so we fall back to a plain timestamped
name (no dedup).

Invoked by the ``Stop`` hook in ``life-os/.claude/settings.json``.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

logger = logging.getLogger("conversation_capture")

LIFE_OS = Path("E:/automation/life-os")
ACTIVE_SKILL_FILE = LIFE_OS / ".active-skill"
SKILLS_DIR = LIFE_OS / ".claude/skills"
ARCHIVE_DIR = LIFE_OS / "conversations/_archive"

KNOWN_SKILLS = {
    "alt-text", "ip-check", "is-this-ai", "journal-daily", "journal-weekly",
    "meeting-prep", "roast-posts", "sparring-private", "sparring-work", "visual-muse",
}

# Tags Claude Code embeds in user messages when a skill is invoked.
_CMD_NAME_RE = re.compile(r"<command-name>/([^<]+)</command-name>")
# Read tool paths that reveal which skill's private files were accessed.
_SKILL_PATH_RE = re.compile(r"life-os[/\\]\.claude[/\\]skills[/\\]([^/\\]+)[/\\]")


def load_transcript(path: Path) -> list[dict]:
    entries = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.error("Could not read transcript %s: %s", path, exc)
    return entries


def _text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
        return " ".join(p for p in parts if p)
    return ""


def extract_messages(entries: list[dict]) -> list[tuple[str, str]]:
    """Return [(role, text), ...] for user/assistant turns."""
    messages = []
    for entry in entries:
        role = entry.get("type")
        if role not in ("user", "assistant"):
            continue
        msg = entry.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else entry.get("content", "")
        text = _text_from_content(content).strip()
        if text:
            messages.append((role, text))
    return messages


def infer_skill_from_transcript(entries: list[dict]) -> Optional[str]:
    """Best-effort skill name from command-name tags or Read tool paths."""
    for entry in entries:
        if entry.get("type") != "user":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        text = _text_from_content(content)
        m = _CMD_NAME_RE.search(text)
        if m:
            candidate = m.group(1).strip()
            if candidate in KNOWN_SKILLS:
                return candidate
    # Second pass: look for Read calls that touched a skill's private dir.
    for entry in entries:
        for field in (entry.get("tool_input", {}), entry.get("message", {})):
            if not isinstance(field, dict):
                continue
            path_str = str(field.get("file_path", "") or field.get("path", ""))
            m = _SKILL_PATH_RE.search(path_str)
            if m:
                candidate = m.group(1)
                if candidate in KNOWN_SKILLS:
                    return candidate
    return None


def _strip_command_tags(text: str) -> str:
    text = re.sub(r"<[a-z-]+>.*?</[a-z-]+>", " ", text, flags=re.DOTALL)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


_PREAMBLE_PATTERNS = re.compile(
    r"(base directory for this skill|# [\w-]|^---\n|local-command-caveat|skill\.md)",
    re.IGNORECASE,
)


def _is_preamble(text: str) -> bool:
    """True if this user message is a skill-loading or command-caveat injection, not a real turn."""
    return bool(_PREAMBLE_PATTERNS.search(text[:300]))


def make_description(messages: list[tuple[str, str]]) -> str:
    """One-line description from the first real user turn (skips skill-loading preamble)."""
    for role, text in messages:
        if role != "user":
            continue
        clean = _strip_command_tags(text)
        if "***" in clean:
            clean = clean.split("***", 1)[1].strip()
        if len(clean) < 10:
            continue
        if _is_preamble(text):
            continue
        if len(clean) <= 120:
            return clean
        cut = clean[:120].rsplit(" ", 1)[0]
        return cut + "…"
    return "session"


def make_slug(description: str) -> str:
    """2-3 significant words from the description, hyphen-joined."""
    words = re.findall(r"[a-z]+", description.lower())
    stopwords = {"i", "a", "an", "the", "and", "or", "to", "in", "on", "of",
                 "is", "it", "my", "me", "we", "you", "he", "she", "ok", "okay",
                 "want", "have", "had", "was", "are", "for", "with", "that",
                 "this", "so", "but", "not", "be"}
    significant = [w for w in words if w not in stopwords and len(w) > 2]
    return "-".join(significant[:3]) if significant else "session"


def session_token(session_id: str) -> str:
    """Short, filename-safe, stable token identifying this session.

    The ``Stop`` hook fires at every turn-end, so one session produces several
    captures. A token derived from the (stable) session id lets each capture
    recognise and supersede its predecessors. Empty when no session id is
    available — the caller then skips dedup.
    """
    cleaned = re.sub(r"[^a-z0-9]", "", (session_id or "").lower())
    return cleaned[-8:]


def capture_filename(timestamp: str, slug: str, token: str) -> str:
    """Build the capture filename. The session token is appended when present
    so :func:`supersede_prior` can find and remove this session's earlier files."""
    if token:
        return f"{timestamp}-{slug}-{token}.md"
    return f"{timestamp}-{slug}.md"


def supersede_prior(out_dir: Path, token: str) -> None:
    """Delete earlier captures of this session before writing the new one.

    No-op without a token. Matching is by the ``-<token>.md`` suffix, so a
    readiness-only first capture is replaced by the full final one rather than
    left behind as a duplicate.
    """
    if not token:
        return
    for prior in out_dir.glob(f"*-{token}.md"):
        try:
            prior.unlink()
        except OSError:
            pass


def render_markdown(description: str, messages: list[tuple[str, str]]) -> str:
    lines = [description, ""]
    for role, text in messages:
        if role == "user" and _is_preamble(text):
            continue
        label = "**You**" if role == "user" else "**Claude**"
        clean = _strip_command_tags(text) if role == "user" else text
        if not clean.strip():
            continue
        lines.append(f"{label}: {clean}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    payload = _lib.read_stdin_json()

    transcript_path_raw = payload.get("transcript_path")
    if not transcript_path_raw:
        return 0
    transcript_path = Path(transcript_path_raw)
    if not transcript_path.exists():
        return 0

    session_id = payload.get("session_id", "")

    # 1. Determine active skill.
    skill: Optional[str] = None
    if ACTIVE_SKILL_FILE.exists():
        try:
            skill = ACTIVE_SKILL_FILE.read_text(encoding="utf-8").strip()
            ACTIVE_SKILL_FILE.unlink()
        except OSError:
            skill = None
    if skill and skill not in KNOWN_SKILLS:
        skill = None

    entries = load_transcript(transcript_path)
    if not skill:
        skill = infer_skill_from_transcript(entries)

    # 2. Build output path.
    if skill:
        out_dir = SKILLS_DIR / skill / "conversations"
    else:
        out_dir = ARCHIVE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # 3. Render.
    messages = extract_messages(entries)
    if not messages:
        return 0  # nothing to capture (pure setup/command sessions)
    description = make_description(messages)
    slug = make_slug(description)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    content = render_markdown(description, messages)

    # The Stop hook fires at every turn-end, so collapse this session's earlier
    # captures (e.g. a cold-start readiness-ack turn) into one up-to-date file.
    token = session_token(session_id)
    supersede_prior(out_dir, token)
    filename = capture_filename(timestamp, slug, token)

    out_path = out_dir / filename
    try:
        out_path.write_text(content, encoding="utf-8")
        logger.info("Captured → %s", out_path)
    except OSError as exc:
        logger.error("Could not write %s: %s", out_path, exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
