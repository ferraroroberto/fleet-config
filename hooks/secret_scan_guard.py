"""Block a live secret before it is committed or published to GitHub.

Triggers on `PreToolUse` for `Bash`/`PowerShell`, on two independent sinks:

- **`git commit`** — scans the **staged diff** (`git diff --cached`) and the
  command string itself for a real credential.
- **`gh issue|pr create|comment|edit` and `gh pr review`** — scans the command
  string (inline `--body`/`-b`/`--title`) plus the contents of every file named
  by `--body-file`/`-F`, resolved against the payload `cwd` (fleet-config#959)
  as moved by any leading `cd`/`Set-Location`/`pushd` (fleet-config#1054).
  Reading the file the command names is what makes the bytes scanned the bytes
  published. Issue and PR bodies on a public repo are world-readable and indexed
  the moment they post, and `gh_body_file_guard` steers bodies into exactly the
  `--body-file` this reads. Fail-open: `-F -` (stdin), a `$VAR`/`$(…)` operand,
  a relative file after a non-literal `cd $X`, or a missing/unreadable file is
  allowed, with one info-level breadcrumb on stderr for the last two cases so a
  miss stays diagnosable.

On the commit side, the one pattern that matters across this fleet is a
Telegram **bot token** and Slack `xoxb-…` alike: the user keeps creds in a secret-managed
location (`.env` / `TELEGRAM_BOT_TOKEN`), never in a tracked file. This is the wire
that catches the mistake before a token lands in `git log` (fleet-config#74).

Why scan the staged diff, not just the command string: the no-AI-trailer guard
only needs the commit *message*, which lives in the command. A leaked secret
instead lives in a **file** being committed, so the command string alone is
blind to it — we have to look at what's actually staged.

Matching is deliberately narrow so it never trips on this repo's own docs, which
legitimately contain the *placeholder* forms `xoxb-…` and `xoxb-<token>`: a real
token has a long secret body and the placeholders do not.

What counts as a credential is **not** decided here — `_lib.SECRET_PATTERNS` is
the one definition for this tier, shared with `context_filter`'s redactor
(fleet-config#561). This module used to carry its own one-family copy, which had
drifted strictly narrower than the redactor's four, so the guard blocked a Telegram
token and waved through an OpenAI key, a GitHub PAT, and an AWS access key id.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


def _is_git_commit(cmd: str) -> bool:
    return "git" in cmd and "commit" in cmd


def _staged_diff(repo_cwd: Path) -> str:
    """Return the staged diff for the repo at ``repo_cwd`` (best-effort).

    Any failure (not a repo, git missing, timeout) yields ``""`` — the guard then
    falls back to scanning just the command string and never blocks spuriously.

    Routed through :func:`_lib.run_git` rather than a hand-rolled
    ``subprocess.run(["git", …])`` (fleet-config#677): this fires on **every
    commit in every repo in the fleet**, and a raw spawn silently opts out of
    ``GIT_OPTIONAL_LOCKS=0`` — the one-line fix that exists precisely because
    ``git diff`` takes ``.git/index.lock`` to write back a refreshed stat cache,
    and a hook killed mid-refresh strands a 0-byte lock that blocks every write
    in that repo while every read keeps exiting 0 (fleet-config#667).
    """
    try:
        res = _lib.run_git(["-C", str(repo_cwd), "diff", "--cached"], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout or ""


# `gh pr review` publishes a body too; `gh api` / `gh gist` / releases are out of
# scope (fleet-config#959), and `gh issue view` / `gh pr list` never match.
GH_PUBLISH_RE = re.compile(r"\bgh\s+(?:issue|pr)\s+(?:create|comment|edit|review)\b")

# Enough for any real issue body; a larger file is scanned up to this prefix.
MAX_BODY_BYTES = 1 << 20

# A `\`-newline (Bash) or backtick-newline (PowerShell) continuation keeps one
# command on one logical line, so `--body-file` on the next line still counts.
_CONTINUATION_RE = re.compile(r"[\\`]\r?\n")
_SEGMENT_SPLIT_RE = re.compile(r"[\n;|&]+")

# Shell-ish words, quotes stripped, backslashes left alone. `shlex` is not
# usable: POSIX mode eats the backslashes out of every Windows path. A quoted
# run inside a word (`--body-file="C:/a b/pr.md"`) stays part of that word.
_WORD_RE = re.compile(r"""(?:[^\s'"]+|'[^']*'|"[^"]*")+""")
_QUOTED_RE = re.compile(r"""'([^']*)'|"([^"]*)\"""")

# Git Bash hands us MSYS paths; `Path("/e/automation/x")` has no drive on
# Windows, so translate it back to the drive form before resolving.
_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])/(.*)$")

# Segment verbs that move the directory a later relative `--body-file` resolves
# against (Bash and PowerShell spellings; PowerShell's are case-insensitive).
_CD_VERBS = frozenset({"cd", "set-location", "sl", "chdir", "pushd", "push-location"})
_POP_VERBS = frozenset({"popd", "pop-location"})


def _words(text: str) -> List[str]:
    return [
        _QUOTED_RE.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(2), word)
        for word in _WORD_RE.findall(text)
    ]


def _cd_target(words: List[str]) -> Optional[str]:
    """The one literal directory operand of a `cd`-like segment, else ``None``.

    Option words (`-P`, `-Path`, `-LiteralPath`, …) are skipped; zero or several
    remaining operands, or `cd -`, name no directory we can follow.
    """
    operands = [w for w in words[1:] if not w.startswith("-")]
    return operands[0] if len(operands) == 1 else None


def body_file_operands(cmd: str, base: Path) -> List[Tuple[str, Optional[Path]]]:
    """``(operand, base)`` for every `--body-file`/`-F` of a `gh` publish segment.

    Covers `--body-file X`, `--body-file=X`, `-F X` and `-FX`. Only the words
    after a `gh issue|pr <publish verb>` in the same segment are read, so a
    `grep -F` elsewhere in the chain never names a file to scan.

    ``base`` starts at the payload cwd and follows every `cd`/`Set-Location`/
    `pushd`-style segment before the gh one (fleet-config#1054): `cd dir && gh
    issue create --body-file rel.md` publishes `dir/rel.md`. A directory the
    hook cannot read literally (`cd $X`, `cd -`, `popd`) makes the base
    ``None`` until a later absolute `cd`.
    """
    operands: List[Tuple[str, Optional[Path]]] = []
    current: Optional[Path] = base
    for segment in _SEGMENT_SPLIT_RE.split(_CONTINUATION_RE.sub(" ", cmd)):
        match = GH_PUBLISH_RE.search(segment)
        if not match:
            words = _words(segment)
            verb = words[0].lower() if words else ""
            if verb in _CD_VERBS:
                target = _cd_target(words)
                current = _resolve(target, current) if target is not None else None
            elif verb in _POP_VERBS:
                current = None
            continue
        words = _words(segment[match.end():])
        i = 0
        while i < len(words):
            word = words[i]
            if word in ("--body-file", "-F"):
                if i + 1 < len(words):
                    operands.append((words[i + 1], current))
                i += 2
                continue
            if word.startswith("--body-file="):
                operands.append((word[len("--body-file="):], current))
            elif word.startswith("-F") and len(word) > 2:
                operands.append((word[2:].lstrip("="), current))
            i += 1
    return operands


def _resolve(raw: str, base: Optional[Path]) -> Optional[Path]:
    """``raw`` as a path, or ``None`` when the hook cannot know where it points.

    `-` is stdin; a `$VAR`, `$(…)` or backtick operand is expanded by the shell
    only after the hook has run; a relative path under an unknown base (after
    `cd $X`) has nowhere to resolve.
    """
    text = raw.strip()
    if not text or text == "-" or "$" in text or "`" in text or text.startswith("("):
        return None
    msys = _MSYS_DRIVE_RE.match(text)
    if msys:
        text = f"{msys.group(1).upper()}:/{msys.group(2)}"
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    return base / path if base is not None else None


def resolve_operand(raw: str, base: Optional[Path]) -> Optional[Path]:
    """A body-file operand as a path to read, or ``None`` (fail-open).

    A literal relative operand whose base a preceding `cd $X` made unknown
    leaves an info breadcrumb, so the unscanned publish stays diagnosable.
    """
    path = _resolve(raw, base)
    if path is None and base is None and _resolve(raw, Path(".")) is not None:
        _lib.logger.info("ℹ️ secret_scan_guard: body file not scanned (%s): "
                         "a preceding cd names no literal directory", raw)
    return path


def _read_body(path: Path) -> Optional[str]:
    try:
        with path.open("rb") as fh:
            data = fh.read(MAX_BODY_BYTES)
    except OSError as exc:
        _lib.logger.info("ℹ️ secret_scan_guard: body file not scanned (%s): %s", path, exc)
        return None
    # Windows PowerShell 5.1's `Out-File` / `>` write UTF-16 LE with a BOM.
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8", errors="replace")


def _guard_gh_publish(cmd: str, base: Path) -> None:
    """Block when the command or a body file it names carries a live secret."""
    parts = [cmd]
    for raw, raw_base in body_file_operands(cmd, base):
        path = resolve_operand(raw, raw_base)
        body = _read_body(path) if path is not None else None
        if body:
            parts.append(body)
    hit = _lib.scan_for_secret("\n".join(parts))
    if hit:
        _lib.block(
            "Blocked: a live secret is about to be published to GitHub (" + hit[0] + "). "
            "Issue and PR bodies and comments are world-readable on a public repo "
            "and effectively unretractable once posted. Redact the token from the "
            "body file (or the inline --body/--title) and retry. If this is a false "
            "positive on a placeholder, shorten the token body so it no longer "
            "looks live."
        )


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    # The gh trigger is the tight one, so it runs first: a gh command whose
    # inline body merely mentions "git commit" gets the publish refusal.
    if GH_PUBLISH_RE.search(cmd):
        logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
        _guard_gh_publish(cmd, _lib.cwd(payload))

    if not _is_git_commit(cmd):
        _lib.allow()

    # Scan both the staged content and the command string itself (a secret could
    # ride in via an inline `git add` + commit one-liner, or a heredoc).
    haystack = cmd + "\n" + _staged_diff(_lib.cwd(payload))

    hit = _lib.scan_for_secret(haystack)
    if hit:
        label, pattern = hit
        _lib.block(
            "Blocked: a live secret is staged for commit (" + label + "). "
            "The user keeps credentials in a secret-managed location (.env / "
            "TELEGRAM_BOT_TOKEN), never in a tracked file. Unstage the file, move "
            "the value into .env (or the OS keyring), and reference it from there "
            "before committing. If this is a false positive on a placeholder, "
            "redact the token body so it no longer looks live."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
