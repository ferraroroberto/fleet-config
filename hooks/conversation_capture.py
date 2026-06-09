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

One file per conversation: the ``Stop`` hook fires at every turn-end (not once
per session), so a single session triggers several captures — e.g. a cold-start
readiness-ack turn, then the real work. Each run supersedes earlier captures of
the same conversation on either of two stable identifiers and writes the latest,
fullest transcript — collapsing the conversation to a single file:

  * the **session token** (last 8 of ``session_id``) collapses turn-end captures
    *within one session*, including the empty cold-start readiness-ack capture
    whose first real turn hasn't appeared yet;
  * the **content signature** (hash of the first real user turn) collapses a
    *resumed* conversation onto its predecessor. ``claude --resume`` copies the
    transcript forward but rewrites every entry's ``session_id`` (and message
    ``uuid``), so the session token alone leaves a duplicate — the first real
    user turn is the only identity that survives a resume.

Without a session id *and* without a real turn we can't identify siblings, so we
fall back to a plain timestamped name (no dedup).

Invoked by the ``Stop`` hook in ``life-os/.claude/settings.json``.
"""

from __future__ import annotations

import hashlib
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


def first_real_turn(messages: list[tuple[str, str]]) -> str:
    """Cleaned full text of the first substantive user turn, or ``""`` if none.

    Skips skill-loading preamble and command-tag injections. The one-line
    description and the dedup content signature both key off this single turn, so
    they always move together — and because ``claude --resume`` copies this turn
    forward verbatim, it is the conversation's only resume-stable identity. (The
    filename *slug* is derived separately from the whole conversation — see
    :func:`conversation_slug`.)
    """
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
        return clean
    return ""


def make_description(messages: list[tuple[str, str]]) -> str:
    """One-line description from the first real user turn (skips skill-loading preamble)."""
    clean = first_real_turn(messages)
    if not clean:
        return "session"
    if len(clean) <= 120:
        return clean
    cut = clean[:120].rsplit(" ", 1)[0]
    return cut + "…"


# Words too generic to make a representative slug: grammatical glue plus the
# conversational filler ("today", "really", "think", …) that dominates a casual
# Life OS chat. Topic-bearing nouns survive the filter; "day"/"today" are dropped
# deliberately — issue #84 cites "day-today-which" as a canonical bad slug.
_STOPWORDS = {
    "i", "a", "an", "the", "and", "or", "to", "in", "on", "of", "is", "it",
    "my", "me", "we", "you", "he", "she", "ok", "okay", "want", "have", "had",
    "was", "are", "for", "with", "that", "this", "so", "but", "not", "be",
    "today", "day", "just", "really", "think", "know", "like", "yeah", "well",
    "going", "get", "got", "thing", "things", "stuff", "kind", "sort", "much",
    "more", "also", "then", "what", "when", "which", "how", "why", "can",
    "will", "would", "could", "should", "did", "does", "make", "way", "about",
    "here", "there", "now", "all", "let", "tell", "said", "say", "were", "been",
    "your", "our", "their", "into", "from", "yes", "maybe", "actually", "sure",
    "gonna", "wanna", "some", "one", "out", "they", "them", "his", "her",
}


def _significant_words(text: str) -> list[str]:
    """Lowercase alphabetic words from ``text``, minus stopwords and short noise."""
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]


def make_slug(description: str) -> str:
    """2-3 significant words from a single string, hyphen-joined (fallback path)."""
    significant = _significant_words(description)
    return "-".join(significant[:3]) if significant else "session"


def conversation_slug(messages: list[tuple[str, str]]) -> str:
    """Slug from the *whole* conversation's most salient words, not just its opener.

    Counts significant words across every non-preamble user/assistant turn and
    keeps the three most frequent — the recurring topic words a conversation
    keeps returning to, rather than the vague line it happened to open with
    (issue #84). Ties break by first appearance, so the result is deterministic
    and stable. Falls back to the first-turn heuristic when the conversation
    holds no significant words yet (e.g. a cold-start readiness-ack capture)."""
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for index, (role, text) in enumerate(messages):
        if role == "user":
            if _is_preamble(text):
                continue
            text = _strip_command_tags(text)
        for word in _significant_words(text):
            counts[word] = counts.get(word, 0) + 1
            first_seen.setdefault(word, index)
    if not counts:
        return make_slug(make_description(messages))
    ranked = sorted(counts, key=lambda w: (-counts[w], first_seen[w]))
    return "-".join(ranked[:3])


def session_token(session_id: str) -> str:
    """Short, filename-safe, stable token identifying this session.

    The ``Stop`` hook fires at every turn-end, so one session produces several
    captures. A token derived from the (stable) session id lets each capture
    recognise and supersede its predecessors. Empty when no session id is
    available — the caller then skips dedup.
    """
    cleaned = re.sub(r"[^a-z0-9]", "", (session_id or "").lower())
    return cleaned[-8:]


def content_signature(messages: list[tuple[str, str]]) -> str:
    """Resume-stable dedup token: a short hash of the first real user turn.

    ``claude --resume`` copies that turn forward verbatim while rewriting the
    ``session_id``, so this signature (unlike :func:`session_token`) lets a
    resumed capture recognise and supersede its predecessor. Empty when no real
    turn has appeared yet (a cold-start readiness-ack capture) — dedup then
    relies on the session token alone.
    """
    clean = first_real_turn(messages)
    if not clean:
        return ""
    return hashlib.sha1(clean.encode("utf-8")).hexdigest()[:8]


def capture_filename(timestamp: str, slug: str, sid_token: str, sig_token: str) -> str:
    """Build the capture filename, embedding both dedup identifiers.

    The session token then the content signature are appended when present (in
    that fixed order), so a later capture can find and supersede this file by
    *either* identifier. With neither we fall back to a plain timestamped name.
    """
    suffix = "".join(f"-{t}" for t in (sid_token, sig_token) if t)
    return f"{timestamp}-{slug}{suffix}.md"


def supersede_prior(out_dir: Path, sid_token: str, sig_token: str) -> None:
    """Delete earlier captures of this conversation before writing the new one.

    Matches on *either* identifier so both the intra-session readiness-ack
    capture and a prior *resumed* capture are collapsed rather than left behind:

      * ``*-<sig>.md`` — the content signature is always the final segment, so
        this catches any earlier capture of the same conversation regardless of
        its (rewritten) session token.
      * ``*-<sid>.md`` / ``*-<sid>-*.md`` — the session token, whether it is the
        final segment (legacy single-token or degenerate captures) or the middle
        one (the new two-token shape).

    No-op when both tokens are empty.
    """
    patterns: list[str] = []
    if sig_token:
        patterns.append(f"*-{sig_token}.md")
    if sid_token:
        patterns.append(f"*-{sid_token}.md")
        patterns.append(f"*-{sid_token}-*.md")
    for pattern in patterns:
        for prior in out_dir.glob(pattern):
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
    slug = conversation_slug(messages)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    content = render_markdown(description, messages)

    # The Stop hook fires at every turn-end, so collapse this conversation's
    # earlier captures into one up-to-date file — keyed on the session token
    # (intra-session, incl. the cold-start readiness-ack turn) and the content
    # signature (a resumed conversation, whose session_id has been rewritten).
    sid_token = session_token(session_id)
    sig_token = content_signature(messages)
    supersede_prior(out_dir, sid_token, sig_token)
    filename = capture_filename(timestamp, slug, sid_token, sig_token)

    out_path = out_dir / filename
    try:
        out_path.write_text(content, encoding="utf-8")
        logger.info("Captured → %s", out_path)
    except OSError as exc:
        logger.error("Could not write %s: %s", out_path, exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
