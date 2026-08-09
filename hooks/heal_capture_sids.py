"""One-shot backfill of resume identity into captures written before #586.

``conversation_capture`` now stamps every capture with the full ``session_id``
(the identity ``claude --resume`` needs). Captures written before that carry
only the *last 8 chars* in their filename, or — for the earliest ones — nothing
at all. This recovers the full id for them from Claude Code's own transcript
store, where each session is a JSONL file **named by its full session id**.

Three matching tiers, tried in order, because the captures are not uniform:

  1. **Filename session token** — the 8-char tail the capture already carries;
     match a transcript whose session id ends with it. Cheapest and exact.
  2. **Filename content signature** — the other 8-char token; match a transcript
     whose first substantive user turn hashes to it.
  3. **Direct content match** — hash the capture's *own* first ``**You**:`` turn
     and compare against every transcript's first-turn hash.

Tier 3 is load-bearing, not a courtesy fallback. Verified on this host: the
earliest captures carry **no tokens at all** (they predate the token naming),
and app-launcher's conversation-rename feature rewrites a filename down to its
date prefix plus a new slug, discarding both tokens. For those, the opening
turn is the only surviving identity.

Two rules keep this honest:

  * **Never fabricate.** A capture whose transcript has been pruned by Claude
    Code's own cleanup gets *no* header and is reported as unresumable. It stays
    fully searchable; it simply cannot be reopened, and saying so is the correct
    outcome (the fleet rule that a check which cannot establish a fact reports
    that rather than folding it into the passing state).
  * **Preserve mtime.** Writing a header would otherwise look like an edit to
    every downstream consumer and trigger a re-digest of the entire archive
    through the LLM hub. ``conversation_index`` picks the recovered id up on its
    next run via a cheap header read instead.

Idempotent: a capture that already carries a header is skipped, so a second run
is a no-op. Run-once by design — delete this module once the fleet's history is
healed (the life-os#57 precedent for one-time backfills).

Usage::

    …/python.exe hooks/heal_capture_sids.py --project life-os --dry-run
    …/python.exe hooks/heal_capture_sids.py --project life-os
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
from conversation_capture import (  # noqa: E402
    CaptureConfig,
    _is_preamble,
    _strip_command_tags,
    capture_config_from_project,
    capture_header,
    normalize_turn,
    parse_capture_header,
    session_token,
    signature_of,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

logger = logging.getLogger("heal_capture_sids")

# Claude Code stores transcripts under a per-project directory whose name is the
# project path with every non-alphanumeric character replaced by a dash
# (``E:\automation\life-os`` -> ``E--automation-life-os``).
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_TOKEN_RE = re.compile(r"-([0-9a-f]{8})(?=[-.])")
_YOU_RE = re.compile(r"^\*\*You\*\*:\s*(.*)$")
_CAPTURE_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-")

# A turn that is nothing but a bracketed marker — `[Request interrupted by
# user]` and friends — is Claude Code's own system text, not something the user
# said. Dozens of unrelated conversations open with the identical string, so
# matching on it pairs a capture with whichever transcript happened to sort
# newest: observed live, a 2026-06-01 capture matched a 2026-07-17 transcript.
# A wrong resume target is worse than none, so such a turn carries no identity.
_SYSTEM_MARKER_RE = re.compile(r"^\[[^\]]+\]$")

# A transcript cannot be the source of a capture written before that transcript
# began. One day of slack absorbs the timezone gap between the UTC timestamps in
# a transcript and the local-time date in a capture filename.
_START_SLACK_DAYS = 1


def project_transcript_dir(root: Path) -> Path:
    return CLAUDE_PROJECTS_DIR / re.sub(r"[^A-Za-z0-9]", "-", str(root))


@dataclass
class Transcript:
    sid: str
    mtime: float
    signature: str
    started: Optional[date] = None


def identity_signature(clean: str) -> str:
    """Signature of an opening turn, or ``""`` when it carries no identity.

    The one place the "is this turn actually distinguishing?" rule lives, so the
    capture side and the transcript side can never disagree about it.
    """
    if not clean or _SYSTEM_MARKER_RE.match(clean.strip()):
        return ""
    return signature_of(clean)


@dataclass
class Report:
    healed: "dict[str, int]" = field(default_factory=dict)
    unmatched: "dict[str, list]" = field(default_factory=dict)
    already: int = 0

    def total_healed(self) -> int:
        return sum(self.healed.values())

    def total_unmatched(self) -> int:
        return sum(len(v) for v in self.unmatched.values())


# --------------------------------------------------------------- transcripts


def first_turn_of_transcript(path: Path) -> str:
    """The transcript's first substantive user turn, streaming and stopping there.

    Reads line by line and returns as soon as a qualifying turn is found — these
    files run to hundreds of KB and only the opening matters, so parsing whole
    transcripts would make the sweep needlessly slow.
    """
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or '"user"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "user":
                    continue
                msg = entry.get("message", {})
                content = msg.get("content", "") if isinstance(msg, dict) else ""
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") for b in content if isinstance(b, dict)
                    )
                if not isinstance(content, str) or not content.strip():
                    continue
                # `_is_preamble` keys off the raw text, exactly as the capture
                # hook does, so both sides skip the same skill-loading turns.
                if _is_preamble(content):
                    continue
                clean = normalize_turn(_strip_command_tags(content))
                if clean:
                    return clean
    except OSError:
        return ""
    return ""


def transcript_started(path: Path) -> Optional[date]:
    """Date of the transcript's first timestamped entry, or ``None``."""
    try:
        with path.open(encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if '"timestamp"' not in line:
                    continue
                try:
                    stamp = json.loads(line).get("timestamp")
                except json.JSONDecodeError:
                    continue
                if isinstance(stamp, str) and stamp:
                    try:
                        return datetime.fromisoformat(
                            stamp.replace("Z", "+00:00")
                        ).date()
                    except ValueError:
                        return None
    except OSError:
        return None
    return None


def load_transcripts(tdir: Path) -> "list[Transcript]":
    out: "list[Transcript]" = []
    if not tdir.is_dir():
        return out
    for path in sorted(tdir.glob("*.jsonl")):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        out.append(
            Transcript(
                sid=path.stem,
                mtime=mtime,
                signature=identity_signature(first_turn_of_transcript(path)),
                started=transcript_started(path),
            )
        )
    return out


def capture_date(name: str) -> Optional[date]:
    m = _CAPTURE_DATE_RE.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def plausible(transcript: Transcript, written: Optional[date]) -> bool:
    """False when this transcript began after the capture was already written."""
    if written is None or transcript.started is None:
        return True  # can't establish it — don't invent a reason to reject
    return transcript.started <= written + timedelta(days=_START_SLACK_DAYS)


# ------------------------------------------------------------------ captures


def capture_first_turn(text: str) -> str:
    """The capture's first substantive user turn, normalized like the transcript side.

    A rendered capture has already had preamble dropped and command tags
    stripped, so only the substantive-turn rules still need replaying — which is
    exactly what :func:`normalize_turn` owns. Multi-line turns are joined back up
    to the next role label, since the renderer wrote them verbatim.
    """
    lines = text.splitlines()
    buf: "list[str]" = []
    for line in lines:
        m = _YOU_RE.match(line)
        if m:
            if buf:
                clean = normalize_turn("\n".join(buf).strip())
                if clean:
                    return clean
            buf = [m.group(1)]
            continue
        if line.startswith("**Claude**:"):
            if buf:
                clean = normalize_turn("\n".join(buf).strip())
                if clean:
                    return clean
                buf = []
            continue
        if buf:
            buf.append(line)
    if buf:
        return normalize_turn("\n".join(buf).strip()) or ""
    return ""


def filename_tokens(name: str) -> "list[str]":
    """The 8-char hex tokens embedded in a capture filename, in order."""
    return _TOKEN_RE.findall(name)


def match_transcript(
    capture: Path, text: str, transcripts: "list[Transcript]"
) -> "tuple[Optional[Transcript], str]":
    """Best transcript for this capture and the tier that found it, or ``(None, "")``.

    On multiple matches the **newest** wins: a resumed conversation leaves a
    chain of transcripts sharing one opening turn, and only the latest is the
    resumable tip.
    """
    tokens = filename_tokens(capture.name)
    written = capture_date(capture.name)

    def newest(candidates: "list[Transcript]") -> Optional[Transcript]:
        # Every tier filters on plausibility, not just the weakest one: an 8-char
        # token can collide too, and "started after the capture existed" rules a
        # candidate out no matter which tier proposed it.
        viable = [t for t in candidates if plausible(t, written)]
        if not viable:
            return None
        # A transcript that began on the capture's own date is the strongest
        # signal available, so it beats "newest" outright. This also stays
        # correct for a resumed lineage: superseding rewrites the capture's
        # timestamp to the resume, so the *tip* transcript is the same-day one,
        # not the original. Only a genuine first-turn collision between two
        # different days now falls through to the mtime tiebreak.
        if written is not None:
            same_day = [t for t in viable if t.started == written]
            if same_day:
                return max(same_day, key=lambda t: t.mtime)
        return max(viable, key=lambda t: t.mtime)

    for token in tokens:
        hit = newest([t for t in transcripts if session_token(t.sid) == token])
        if hit:
            return hit, "session-token"
    for token in tokens:
        hit = newest([t for t in transcripts if t.signature and t.signature == token])
        if hit:
            return hit, "content-signature"
    sig = identity_signature(capture_first_turn(text))
    if sig:
        hit = newest([t for t in transcripts if t.signature == sig])
        if hit:
            return hit, "content-match"
    return None, ""


def write_header(path: Path, text: str, sid: str, agent: str) -> None:
    """Insert the identity header under the description, preserving mtime."""
    lines = text.splitlines()
    header = capture_header(sid, agent, "")
    # The renderer's shape is `description`, blank, then the body — so the
    # header belongs at index 1, matching where a fresh capture writes it.
    insert_at = 1 if len(lines) > 1 else len(lines)
    new_lines = lines[:insert_at] + ["", header] + lines[insert_at:]
    try:
        st = path.stat()
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        os.utime(path, (st.st_atime, st.st_mtime))
    except OSError as exc:
        logger.error("could not heal %s: %s", path, exc)


# ---------------------------------------------------------------------- run


def heal_project(cfg: CaptureConfig, *, dry_run: bool = False) -> Report:
    import conversation_index as ci

    report = Report()
    transcripts = load_transcripts(project_transcript_dir(cfg.root))
    logger.info("%d transcript(s) available for matching", len(transcripts))

    for conv_dir, label in ci.conversations_dirs(cfg):
        if not conv_dir.is_dir():
            continue
        for path in sorted(conv_dir.glob("*.md")):
            if path.name == ci.INDEX_NAME:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if parse_capture_header(text).get("sid"):
                report.already += 1
                continue
            hit, tier = match_transcript(path, text, transcripts)
            if hit is None:
                report.unmatched.setdefault(label, []).append(path.name)
                continue
            if not dry_run:
                write_header(path, text, hit.sid, "claude")
            report.healed[label] = report.healed.get(label, 0) + 1
            logger.debug("%s <- %s (%s)", path.name, hit.sid, tier)
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    ap = argparse.ArgumentParser(description="Backfill session ids into old captures.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", help="project name from projects.toml")
    g.add_argument("--cwd", help="resolve the project by a cwd path")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    reg = _lib.load_registry()
    if args.project:
        match = next((p for p in reg.projects if p.name == args.project), None)
    else:
        match = _lib.detect_project(Path(args.cwd or "."), reg)
    cfg = capture_config_from_project(match) if match else None
    if cfg is None:
        print("project not found or not opted into capture", file=sys.stderr)
        return 1

    report = heal_project(cfg, dry_run=args.dry_run)
    print(f"{'DRY RUN — ' if args.dry_run else ''}healed {report.total_healed()}, "
          f"already had an id {report.already}, unresumable {report.total_unmatched()}")
    for skill in sorted(set(report.healed) | set(report.unmatched)):
        healed = report.healed.get(skill, 0)
        missed = len(report.unmatched.get(skill, []))
        print(f"  {skill}: +{healed} healed, {missed} unresumable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
