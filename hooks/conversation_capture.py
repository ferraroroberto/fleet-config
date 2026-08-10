"""Stop hook — capture a finished Claude Code session as a markdown file.

Generic + ``projects.toml``-driven (CLAUDE.md: hooks stay generic, project
quirks live in the registry). Fires on every ``Stop`` event; detects the
session's project by ``cwd`` and captures only if that project opted in with
``capture = true``. Otherwise it's a silent no-op. Reads the JSONL transcript
and writes a ``YYYY-MM-DD-HHMM-<slug>.md`` file into the project's conversations
area. life-os is the first opted-in project.

Routing (per the project's ``capture_routing``):
  * ``"flat"`` (default) — one ``<conversations_dir>/`` for the whole project.
  * ``"skills"`` (life-os) — per-skill ``conversations/`` dirs under
    ``<skills_dir>/<skill>/``, with the skill resolved by:
      1. the ``active_marker`` file (e.g. ``.active-skill``, written by the
         skill's Step 0), then
      2. inference from ``<command-name>`` tags / Read paths in the transcript,
      3. else ``<conversations_dir>/_archive/`` so nothing is ever dropped.

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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402

logger = logging.getLogger("conversation_capture")

# Tags Claude Code embeds in user messages when a skill is invoked.
_CMD_NAME_RE = re.compile(r"<command-name>/([^<]+)</command-name>")


# --------------------------------------------------------------- capture config

# Which projects capture, and how, is data in ``projects.toml`` (CLAUDE.md: hooks
# stay generic, project quirks live in the registry). A project opts in with
# ``capture = true``; everything else has a sensible default. life-os is the
# first opted-in project, with ``capture_routing = "skills"``.


@dataclass(frozen=True)
class CaptureConfig:
    root: Path                 # the project's cwd_prefix
    routing: str               # "skills" | "flat"
    conversations_dir: str     # subdir under root that holds captures (flat) / the _archive (skills)
    skills_dir: str            # subdir under root holding per-skill folders (skills routing)
    active_marker: str         # marker file a skill writes to name itself (skills routing)


def capture_config_from_project(project) -> Optional[CaptureConfig]:
    """Build a :class:`CaptureConfig` from a ``projects.toml`` project, or
    ``None`` when it didn't opt in (``capture = true``). Shared by the capture
    hook (resolve-by-cwd) and the indexer (resolve-by-name)."""
    if project is None or not project.extra.get("capture"):
        return None
    extra = project.extra
    return CaptureConfig(
        root=Path(project.cwd_prefix),
        routing=str(extra.get("capture_routing", "flat")),
        conversations_dir=str(extra.get("conversations_dir", "conversations")),
        skills_dir=str(extra.get("skills_dir", ".claude/skills")),
        active_marker=str(extra.get("active_marker", ".active-skill")),
    )


def resolve_capture_config(payload: dict) -> Optional[CaptureConfig]:
    """The capture config for the session's project, or ``None`` if it opted out.

    Detects the project by the payload ``cwd`` (longest ``cwd_prefix`` match in
    ``projects.toml``). A project with no ``capture = true`` is a silent no-op.
    """
    return capture_config_from_project(_lib.detect_project(_lib.cwd(payload)))


def scan_known_skills(skills_root: Path) -> set:
    """Direct child folders of ``skills_root``, minus ``_``-prefixed scaffolding."""
    if not skills_root.is_dir():
        return set()
    try:
        return {p.name for p in skills_root.iterdir() if p.is_dir() and not p.name.startswith("_")}
    except OSError:
        return set()


def _skill_path_re(skills_dir: str) -> "re.Pattern":
    """Regex matching ``<skills_dir>/<skill>/`` in a Read tool path (either slash)."""
    escaped = re.escape(skills_dir).replace("/", r"[/\\]").replace("\\\\", r"[/\\]")
    return re.compile(escaped + r"[/\\]([^/\\]+)[/\\]")


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


def infer_skill_from_transcript(
    entries: list[dict], known_skills: set, skills_dir: str = ".claude/skills"
) -> Optional[str]:
    """Best-effort skill name from command-name tags or Read tool paths."""
    skill_path_re = _skill_path_re(skills_dir)
    for entry in entries:
        if entry.get("type") != "user":
            continue
        msg = entry.get("message", {})
        content = msg.get("content", "") if isinstance(msg, dict) else ""
        text = _text_from_content(content)
        m = _CMD_NAME_RE.search(text)
        if m:
            candidate = m.group(1).strip()
            if candidate in known_skills:
                return candidate
    # Second pass: look for Read calls that touched a skill's private dir.
    for entry in entries:
        for field in (entry.get("tool_input", {}), entry.get("message", {})):
            if not isinstance(field, dict):
                continue
            path_str = str(field.get("file_path", "") or field.get("path", ""))
            m = skill_path_re.search(path_str)
            if m:
                candidate = m.group(1)
                if candidate in known_skills:
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


def normalize_turn(clean: str) -> Optional[str]:
    """Apply the substantive-turn rules to already-tag-stripped text.

    Returns the normalized turn, or ``None`` when the text doesn't qualify as
    substantive. Kept separate from :func:`first_real_turn` so the *same* rules
    can be replayed against a rendered capture's ``**You**:`` blocks: anything
    deriving a conversation's identity from a capture file rather than from a
    live payload has to normalize the opening turn identically, or the two sides
    compute different signatures for the same conversation.
    """
    if "***" in clean:
        clean = clean.split("***", 1)[1].strip()
    if len(clean) < 10:
        return None
    return clean


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
        if _is_preamble(text):
            continue
        clean = normalize_turn(_strip_command_tags(text))
        if clean is None:
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


def signature_of(clean: str) -> str:
    """The short hash used as a conversation's content signature.

    Split from :func:`content_signature` so a caller holding the normalized
    opening turn from *any* source — a live transcript or a rendered capture —
    hashes it exactly the same way.
    """
    if not clean:
        return ""
    return hashlib.sha1(clean.encode("utf-8")).hexdigest()[:8]


def content_signature(messages: list[tuple[str, str]]) -> str:
    """Resume-stable dedup token: a short hash of the first real user turn.

    ``claude --resume`` copies that turn forward verbatim while rewriting the
    ``session_id``, so this signature (unlike :func:`session_token`) lets a
    resumed capture recognise and supersede its predecessor. Empty when no real
    turn has appeared yet (a cold-start readiness-ack capture) — dedup then
    relies on the session token alone.
    """
    return signature_of(first_real_turn(messages))


# ------------------------------------------------------------ capture header

# The machine-readable identity line every capture carries, written directly
# under the human description (fleet-config#586). It exists because the filename
# keeps only the *last 8 chars* of the session id (see `session_token`), which is
# enough to dedup captures but not to resume one: `claude --resume` needs the
# full id. Storing it here — rewritten on every supersede, so it always names the
# session that can actually be resumed — is what makes a specific past
# conversation reopenable from a search result.
#
# Legacy captures predate the header and simply don't have one; every consumer
# treats `sid`/`agent` as optional and degrades to "searchable but not
# resumable" rather than inventing an id.
_CAPTURE_HEADER_RE = re.compile(r"^<!-- capture (?P<attrs>[^>]*?)-->\s*$", re.MULTILINE)
_HEADER_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def capture_header(sid: str, agent: str, updated: str) -> str:
    """Render the identity header. Empty fields are omitted, never written blank."""
    parts = []
    if sid:
        parts.append(f'sid="{sid}"')
    if agent:
        parts.append(f'agent="{agent}"')
    if updated:
        parts.append(f'updated="{updated}"')
    return f"<!-- capture {' '.join(parts)} -->" if parts else ""


def parse_capture_header(text: str) -> dict:
    """``{sid, agent, updated}`` from a capture's header, or ``{}`` when absent.

    Only the first header in the file is honoured, and only when it appears in
    the first few lines — a ``<!-- capture ... -->`` string quoted inside a
    transcript body is conversation content, not identity.
    """
    head = "\n".join(text.splitlines()[:6])
    m = _CAPTURE_HEADER_RE.search(head)
    if not m:
        return {}
    return dict(_HEADER_ATTR_RE.findall(m.group("attrs")))


def strip_capture_header(text: str) -> str:
    """The capture text without its identity header — what a digest should see."""
    lines = text.splitlines()
    kept = [ln for i, ln in enumerate(lines) if not (i < 6 and _CAPTURE_HEADER_RE.match(ln))]
    return "\n".join(kept)


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


def render_markdown(
    description: str,
    messages: list[tuple[str, str]],
    *,
    header: str = "",
) -> str:
    lines = [description, ""]
    if header:
        lines.extend([header, ""])
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

    cfg = resolve_capture_config(payload)
    if cfg is None:
        return 0  # project not opted into capture — silent no-op

    session_id = payload.get("session_id", "")
    entries = load_transcript(transcript_path)

    # 1. Build the output dir from the project's routing.
    if cfg.routing == "skills":
        skills_root = cfg.root / cfg.skills_dir
        known = scan_known_skills(skills_root)
        marker = cfg.root / cfg.active_marker
        skill: Optional[str] = None
        if marker.exists():
            try:
                skill = marker.read_text(encoding="utf-8").strip()
                marker.unlink()
            except OSError:
                skill = None
        if skill and skill not in known:
            skill = None
        if not skill:
            skill = infer_skill_from_transcript(entries, known, cfg.skills_dir)
        if skill:
            out_dir = skills_root / skill / "conversations"
        else:
            out_dir = cfg.root / cfg.conversations_dir / "_archive"
    else:  # "flat" — one conversations dir for the whole project
        out_dir = cfg.root / cfg.conversations_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # 2. Render.
    messages = extract_messages(entries)
    if not messages:
        return 0  # nothing to capture (pure setup/command sessions)
    description = make_description(messages)
    slug = conversation_slug(messages)
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d-%H%M")
    # The full session id is the resume identity (fleet-config#586) — the
    # filename only ever carries its last 8 chars. Written fresh on every
    # capture, so after a resume (which rewrites session_id) the surviving file
    # names the session that can actually be reopened.
    header = capture_header(
        session_id or "",
        _lib.payload_agent(payload) or "claude",
        now.isoformat(timespec="seconds"),
    )
    content = render_markdown(description, messages, header=header)

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
