"""Opt-in Stop capture for native Claude/Codex stored transcripts.

Transcript readers normalize identity and chronological conversational turns
before rendering. One harness-qualified native session updates one capture;
forks remain distinct. Legacy captures remain readable. Digest generation stays
separate and uses the existing hub. See docs/conversation-capture.md.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
from transcript_readers import Transcript, read_transcript  # noqa: E402

logger = logging.getLogger("conversation_capture")

# Tags Claude Code embeds in user messages when a skill is invoked.
_CMD_NAME_RE = re.compile(r"<command-name>/([^<]+)</command-name>")

# Marker provenance (fleet-config#784). The marker and the transcript are stamped
# by the same host clock, so only a token skew allowance is needed; the max age is
# the weaker fallback used when a source records no timestamp at all.
_MARKER_SKEW_SECONDS = 5
_MARKER_MAX_AGE_SECONDS = 12 * 3600


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
    """Regex matching ``<skills_dir>/<skill>/`` in a path (either slash).

    ``skills_dir`` segments are escaped individually and rejoined on a
    slash-or-backslash class; substituting into the *whole* escaped string in
    one pass (the previous approach) leaves a literal ``[/\\]`` in the escaped
    text, and a second blanket substitution over that same string then matches
    its own inserted backslashes and nests the class again — a real bug that
    left this regex, and everything reading its match, permanently dead
    (fleet-config#785).
    """
    parts = [p for p in re.split(r"[/\\]+", skills_dir.strip("/\\")) if p]
    escaped = r"[/\\]".join(re.escape(p) for p in parts)
    return re.compile(escaped + r"[/\\]([^/\\]+)[/\\]")


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


def infer_skill_from_transcript(
    transcript: "Transcript", known_skills: set, skills_dir: str = ".claude/skills"
) -> Optional[str]:
    """Best-effort skill name from command-name tags, tool paths, or a plain
    skill-path mention in conversation text.

    The first two passes read Claude-shaped raw ``entries`` (``<command-name>``
    tags; Read-tool ``file_path``/``path`` values) and are a no-op for a harness
    whose entries don't carry that shape. The third pass is harness-agnostic: it
    scans the reader-normalized ``messages`` for a skill-path mention in a user
    turn's plain text — the shape Codex naturally produces when a skill names
    itself by path ("Use the journal-daily skill from
    .claude/skills/journal-daily/SKILL.md."), with no command tags or
    ``tool_input`` to inspect (fleet-config#785).
    """
    skill_path_re = _skill_path_re(skills_dir)
    for entry in transcript.entries:
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
    for entry in transcript.entries:
        for field in (entry.get("tool_input", {}), entry.get("message", {})):
            if not isinstance(field, dict):
                continue
            path_str = str(field.get("file_path", "") or field.get("path", ""))
            m = skill_path_re.search(path_str)
            if m:
                candidate = m.group(1)
                if candidate in known_skills:
                    return candidate
    # Third pass: a skill-path mention anywhere in plain user-turn text.
    for role, text in transcript.messages:
        if role != "user":
            continue
        m = skill_path_re.search(text)
        if m:
            candidate = m.group(1)
            if candidate in known_skills:
                return candidate
    return None


def transcript_start_time(entries: list[dict]) -> Optional[datetime]:
    """Earliest wall-clock timestamp a native source recorded, or ``None``.

    Both readers' sources stamp entries ISO-8601 UTC, but not every entry: Claude
    interleaves bookkeeping records that carry none, so take the minimum rather
    than trusting the first entry to be stamped.
    """
    earliest = None
    for entry in entries:
        raw = entry.get("timestamp")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            stamped = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
        if earliest is None or stamped < earliest:
            earliest = stamped
    return earliest


def marker_is_current(written_at: float, transcript: Transcript) -> bool:
    """Whether the marker could have been written by the session ending now.

    A skill's Step 0 writes the marker from inside its own session, and that
    session's own Stop hook consumes it a turn later. So a marker predating this
    session's first recorded turn belongs to an *earlier* session whose Stop hook
    never fired, and describes a skill this session never ran (fleet-config#784).
    A source with no timestamps at all cannot prove provenance either way; bound
    it by age instead, which still catches the interrupted-session leftover.
    """
    written = datetime.fromtimestamp(written_at, timezone.utc)
    started = transcript_start_time(transcript.entries)
    if started is not None:
        return written >= started - timedelta(seconds=_MARKER_SKEW_SECONDS)
    return datetime.now(timezone.utc) - written <= timedelta(seconds=_MARKER_MAX_AGE_SECONDS)


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
    description and the legacy content fingerprint key off this turn. Its text
    is descriptive, never native session identity. (The
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
    """Legacy last-eight filename token; never evidence to replace a capture."""
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
    """Legacy descriptive fingerprint, retained for consumers; never dedup identity."""
    return signature_of(first_real_turn(messages))


# ------------------------------------------------------------ capture header

# Legacy sid/agent/updated headers remain readable; new attrs are additive.
_CAPTURE_HEADER_RE = re.compile(r"^<!-- capture (?P<attrs>[^>]*?)-->\s*$", re.MULTILINE)
_HEADER_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def capture_header(sid: str, agent: str, updated: str, **metadata: str) -> str:
    """Render escaped optional identity/provenance attrs; preserve legacy grammar."""
    attrs = dict(sid=sid, agent=agent, updated=updated, **metadata)
    parts = [f'{key}="{html.escape(value, quote=True)}"' for key, value in attrs.items() if value]
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
    return {k: html.unescape(v) for k, v in _HEADER_ATTR_RE.findall(m.group("attrs"))}


def strip_capture_header(text: str) -> str:
    """The capture text without its identity header — what a digest should see."""
    lines = text.splitlines()
    kept = [ln for i, ln in enumerate(lines) if not (i < 6 and _CAPTURE_HEADER_RE.match(ln))]
    return "\n".join(kept)


def capture_filename(timestamp: str, slug: str, sid_token: str, sig_token: str) -> str:
    """Legacy filename constructor retained for consumers; new writes use a truncated key."""
    suffix = "".join(f"-{t}" for t in (sid_token, sig_token) if t)
    return f"{timestamp}-{slug}{suffix}.md"


def render_markdown(
    description: str,
    messages: list[tuple[str, str]],
    *,
    header: str = "",
    agent: str = "",
) -> str:
    """Render one capture body. Preamble/command-tag handling is harness-agnostic
    (fleet-config#785): the same rules strip a Claude skill-loading injection and
    a Codex "Use the X skill from .../SKILL.md" opener alike, so two captures of
    the same conversation differ only in the ``agent`` attribute and speaker label.
    """
    agent = agent or parse_capture_header(header).get("agent", "")
    lines = [description, ""]
    if header:
        lines.extend([header, ""])
    for role, text in messages:
        if role == "user" and _is_preamble(text):
            continue
        assistant = {"claude": "Claude", "codex": "Codex"}.get(agent, "Assistant")
        label = "**You**" if role == "user" else f"**{assistant}**"
        clean = _strip_command_tags(text) if role == "user" else text
        if not clean.strip():
            continue
        lines.append(f"{label}: {clean}")
        lines.append("")
    return "\n".join(lines)


# Buffer above conversation_index's own SETTLE_SECONDS (45s, conversation_index.py)
# so the delayed run lands after a capture that's genuinely done changing, not
# mid-window — a run that fires too early would just skip it and wait for the
# *next* trigger, which is exactly the lazy-SessionStart gap this exists to close.
_INDEX_DELAY_SECONDS = 60.0

INDEXER = Path(__file__).resolve().parent / "conversation_index.py"


def _trigger_delayed_index(project_name: str) -> None:
    """Spawn a detached, delayed ``conversation_index`` run for ``project_name``.

    ``Stop`` fires at every turn-end, so this fires once per turn during an
    active conversation — cheap, because ``conversation_index.py`` is already
    idempotent per capture (its settle window + unchanged-mtime check make a
    run against a capture that hasn't settled, or hasn't changed, a near-instant
    no-op). The one delayed run that lands after the conversation has genuinely
    stopped producing new turns is the one that actually digests it — near the
    close, instead of waiting for the next ``SessionStart`` (fleet-config#673).

    Detached and fail-open, same shape as ``session_index.py``'s own trigger:
    never blocks or delays the ``Stop`` hook, never raises.
    """
    if not INDEXER.exists():
        return
    try:
        py = sys.executable or "py"
        subprocess.Popen(
            [py, str(INDEXER), "--project", project_name,
             "--delay-seconds", str(_INDEX_DELAY_SECONDS)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(INDEXER.parent),
            creationflags=_lib.NO_WINDOW,
        )
    except OSError:
        pass  # fail-open — a failed trigger must never break the Stop hook


def write_capture(
    cfg: CaptureConfig, out_dir: Path, source: Path, transcript: Transcript,
    *, filename_time: Optional[datetime] = None, dry_run: bool = False,
) -> bool:
    """Atomically update an exact native session; no prompt hashes or short-ID matches.

    Search all configured routing folders so later turns cannot strand duplicates
    when the one-shot skill marker has already been consumed. A missing native ID
    uses the source path only for idempotence, never for a resume command.

    ``filename_time`` stamps a *new* capture's filename; it defaults to "now",
    which is correct for a live Stop-hook write. A backfill of a session that
    ended in the past passes the transcript's own recorded start instead, so a
    recovered conversation sorts and files under its real date rather than the
    recovery run's (fleet-config#785). The header's own ``updated`` attribute is
    unaffected — it always records when this capture was actually written.

    ``dry_run`` runs the exact same identity/digest lookup and returns whether a
    write *would* happen, touching no disk — so a caller reporting "would
    capture" can never disagree with what a real run actually does (a naive
    dry-run that skips the dedup lookup entirely would report every already-
    captured session as new, fleet-config#785).
    """
    from conversation_index import _read_header, conversations_dirs

    identity = transcript.session_id or str(source.resolve())
    key = hashlib.sha256(f"{transcript.harness}\0{identity}".encode()).hexdigest()
    digest = hashlib.sha256(json.dumps(transcript.messages, ensure_ascii=False).encode()).hexdigest()
    out_path = None
    for directory, _label in conversations_dirs(cfg):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name == "index.md":
                continue
            # Bounded 6-line header read, not a full-file slurp: the dedup check
            # below only ever inspects the header fields `parse_capture_header`
            # extracts from the first lines. This scan runs once per Stop hook
            # per capture in every routed directory, so re-reading whole
            # transcripts here re-slurps the entire capture corpus on every
            # turn-end for a project routing to many skill directories
            # (fleet-config#819); `conversation_index._read_header` already
            # does the bounded read.
            prior = _read_header(path)
            same = (prior.get("agent") == transcript.harness and
                    ((transcript.session_id and prior.get("sid") == transcript.session_id)
                     or (not transcript.session_id and prior.get("key") == key)))
            if same:
                out_path = path
                try:
                    prior_turns = int(prior.get("turns", "0"))
                except ValueError:
                    prior_turns = 0
                if prior_turns > len(transcript.messages):
                    logger.warning("Capture parse_failure: source shrank; retained prior capture")
                    return False
                if (prior.get("digest") == digest and prior.get("parent_sid", "") == transcript.parent_session_id
                        and prior.get("format") == transcript.source_format):
                    return False
                break
        if out_path:
            break
    if dry_run:
        return True
    now = datetime.now(timezone.utc)
    if out_path is None:
        stamp = filename_time or now
        # Filename token is a truncated *cosmetic* uniquifier only — dedup/resume
        # identity always reads the full `key` from the header (see the header-parsing
        # loop above), never the filename, so truncating here is safe (fleet-config#791).
        filename = f"{stamp:%Y-%m-%d-%H%M}-{conversation_slug(transcript.messages)}-{key[:8]}.md"
        out_path = out_dir / filename
    header = capture_header(transcript.session_id, transcript.harness, now.isoformat(timespec="seconds"),
                            schema="2", key=key, digest=digest, turns=str(len(transcript.messages)),
                            parent_sid=transcript.parent_session_id,
                            format=transcript.source_format, version=transcript.source_version)
    content = render_markdown(make_description(transcript.messages), transcript.messages,
                              header=header, agent=transcript.harness)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=out_path.parent,
                                         prefix=".capture-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, out_path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    logger.info("Captured %s session %s -> %s", transcript.harness, transcript.session_id or "unknown", out_path)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    payload = _lib.read_stdin_json()
    project = _lib.detect_project(_lib.cwd(payload))
    cfg = capture_config_from_project(project)
    if cfg is None or project is None:
        return 0
    allowed = project.extra.get("capture_harnesses", ["claude"])
    if not isinstance(allowed, list):
        logger.error("Capture unsupported: capture_harnesses must be a list")
        return 0
    hint = _lib.payload_agent(payload)
    if hint and hint not in allowed:
        return 0
    raw_path = payload.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path:
        logger.warning("Capture unavailable: no transcript path")
        return 0
    source = Path(raw_path)
    transcript = read_transcript(source, harness=hint, session_id=payload.get("session_id") or "")
    if transcript.status != "ok":
        logger.warning("Capture %s: %s", transcript.status, transcript.detail)
        return 0
    if transcript.harness not in allowed or not transcript.messages:
        return 0

    out_dir = cfg.root / cfg.conversations_dir
    if cfg.routing == "skills":
        skills_root = cfg.root / cfg.skills_dir
        known = scan_known_skills(skills_root)
        marker = cfg.root / cfg.active_marker
        skill, written_at = None, None
        try:
            written_at = marker.stat().st_mtime
            skill = marker.read_text(encoding="utf-8", errors="replace").strip()
            marker.unlink()  # one-shot, even when rejected below — never let it linger
        except OSError:
            skill, written_at = None, None
        if skill not in known:
            skill = None
        elif not marker_is_current(written_at, transcript):
            logger.warning("Capture routing: ignored %r marker predating this session", skill)
            skill = None
        if not skill:
            skill = infer_skill_from_transcript(transcript, known, cfg.skills_dir)
        out_dir = skills_root / skill / "conversations" if skill else out_dir / "_archive"
    try:
        if write_capture(cfg, out_dir, source, transcript):
            _trigger_delayed_index(project.name)
    except (OSError, UnicodeError) as exc:
        logger.error("Capture write failed: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
