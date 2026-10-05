"""Local command-output compression for the fleet hook layer.

The module is intentionally heuristic and deterministic. It optimizes the
high-volume shell output surfaces that show up in agent sessions while keeping
diagnostic lines, file paths, and exit-relevant summaries visible.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


TOKEN_DIVISOR = 4
DEFAULT_MAX_LINES = 80
DEFAULT_MAX_CHARS = 12_000
SMALL_OUTPUT_CHARS = 1_800
GIT_LOG_HEAD_LINES = 40

SIGNAL_RE = re.compile(
    r"("
    r"\b(error|errors|failed|failure|failures|fatal|exception|traceback|assert|warning|warn)\b"
    r"|^\s*(FAILED|ERROR|E\s+|F\s+)"
    r"|\b[A-Za-z0-9_./\\-]+\.py:\d+\b"
    r"|\b[A-Za-z0-9_./\\-]+\.(ts|tsx|js|jsx|go|rs|py|ps1|md):\d+\b"
    r")",
    re.IGNORECASE,
)

# One definition of "what a credential looks like" for the hooks tier, shared
# with `secret_scan_guard`'s commit blocker (fleet-config#561) — extending
# coverage is a one-line change in `_lib.SECRET_PATTERNS`, not two edits that
# can drift apart.
SECRET_RE = _lib.SECRET_RE

# A helper shipped with a skill (fleet_audit_scan.py, chief_ops.py,
# worktree_claim.py, the design_lint package, ...) prints one payload its
# caller reads whole, often JSON, so compressing or truncating it has no upside
# and a wrapper-timeout truncation is strictly worse than the command timing
# out on its own terms (fleet-config#424). Some are long-running by design:
# `.claude/skills/fleet-health/capture.py` blocks for `POLL_CHUNK_S = 540`
# seconds, 60s under the 600s wrapper cap (fleet-config#427), and `design_lint`
# is an extension-less package directory (fleet-config#564).
#
# Matched only where a python interpreter *runs* something under a skill
# directory (fleet-config#1268). The former pattern matched any
# `skills/<dir>/<file>.py` anywhere in the command, so reading a helper's
# source (`sed -n`, `grep -n`, `cat`) was excluded as if it were a sweep.
SKILL_HELPER_RUN_RE = re.compile(
    r"(?:^|[\s/\\\"'&;(|])(?:python3?|py)(?:\.exe)?[\"']?\s+[^|;&\n]*?skills[/\\][^\s'\"/\\]+[/\\]",
    re.IGNORECASE,
)

# Leading `cd <dir> &&` / `cd <dir>;` segments (fleet-config#1268). The
# harness's shell persists its cwd between tool calls, so the hook leaves these
# in the outer shell and wraps only what follows them; the wrapper then runs in
# the directory the prefix moved to.
CD_PREFIX_RE = re.compile(r"""\s*cd\s+(?:"[^"\n]*"|'[^'\n]*'|[^\s;&|'"<>()]+)\s*(?:&&|;)\s*""")
# `VAR=val cmd`: bash scopes the assignments to that one command, so the whole
# string runs unchanged and only classification skips them.
ENV_PREFIX_RE = re.compile(r"""(?:[A-Za-z_][A-Za-z0-9_]*=(?:"[^"\n]*"|'[^'\n]*'|[^\s;&|'"<>()]*)\s+)+""")
HEREDOC_RE = re.compile(r"""(?<!<)<<-?\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\1""")
# Shell state that would land in the wrapper's throwaway shell instead of the
# harness's persistent one.
STATE_MUTATION_RE = re.compile(
    r"(?:^|[;&|\n({])\s*(?:cd|pushd|popd|export|source|\.|set|unset|alias|set-location)(?:\s|$)",
    re.IGNORECASE,
)
# stdout to a file or /dev/null: nothing comes back to the agent to compress.
OUTPUT_TO_FILE_RE = re.compile(
    r"(?:^|[\s;|&(])[1&]?>>?(?![&>])|\|\s*(?:out-file|set-content|add-content|out-null)\b",
    re.IGNORECASE,
)
# A lone `&` after a word is bash's background operator: the job would outlive
# the wrapper still holding its pipes. A PowerShell call operator follows an
# operator or starts the statement, so it never matches.
BACKGROUND_RE = re.compile(r"[^\s;|&(){}<>]\s*&(?![&>])")
# Syntax pwsh 7 (the PowerShell tool) accepts and the wrapper's Windows
# PowerShell 5.1 rejects with a ParserError.
PS7_ONLY_RE = re.compile(r"&&|\|\||\?\?")
COMPOUND_RE = re.compile(r"(?<!\|)\|(?!\|)|&&|\|\||;|\n")

# Reading a file or tailing a log has no summarisable semantics: the content IS
# the payload, so there is nothing a summary can faithfully stand in for. The
# floor for these commands is therefore the whole char budget — under it the
# output comes back verbatim, over it byte-truncated with an explicit marker
# naming how many lines were withheld (fleet-config#837).
#
# What this replaces: a `cat`/`tail` used to come back as only the lines
# matching SIGNAL_RE, which turned a 433-line source file into ten lines
# containing the word "fail" and a monitoring digest into its five least
# informative rows — both read at face value as "there is nothing there". The
# issue's own phrasing is the rule adopted here: returning the raw output
# truncated at a byte limit is strictly more honest than a summary the agent
# cannot tell from an empty result.
CONTENT_COMMANDS = {"cat", "tail"}

# JSON output is payload on the same terms (fleet-config#1269). It used to come
# back as `JSON object: 3 top-level keys; keys: body, number, title` -- the
# values gone, and a single-line output got no retrieve footer either, so
# `gh issue view --json body` read as an issue with no body.


@dataclass(frozen=True)
class CompressionResult:
    command: str
    raw: str
    compressed: str
    raw_tokens: int
    compressed_tokens: int
    reduction_pct: float
    line_count: int
    compressed_line_count: int
    duration_ms: float
    raw_key: Optional[str]
    secret_like: bool
    # True when any of the (redacted) output is not in `compressed` -- the
    # condition for pointing the reader at the cached raw output.
    withheld: bool


@dataclass(frozen=True)
class RewriteDecision:
    should_wrap: bool
    reason: str
    command: str
    # When wrapping: `prefix` stays in the harness's shell (leading `cd`
    # segments, verbatim) and `body` is what the wrapper runs. They concatenate
    # back to `command`.
    prefix: str = ""
    body: str = ""


def estimate_tokens(text: str) -> int:
    """Return a stable, cheap token estimate suitable for before/after deltas."""
    if not text:
        return 0
    return max(1, (len(text) + TOKEN_DIVISOR - 1) // TOKEN_DIVISOR)


VALID_MODES = {"off", "shadow", "rewrite"}
BLOB_TTL_SECONDS = 7 * 24 * 3600


def data_dir() -> Path:
    # FLEET_CONTEXT_FILTER_DIR exists so tests (and e2e harnesses) can isolate
    # the mode file, shadow log, and blob cache away from the machine's live
    # telemetry; unset means the real per-user directory.
    override = os.environ.get("FLEET_CONTEXT_FILTER_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".fleet-context-filter"


def mode_file_path() -> Path:
    return data_dir() / "mode.json"


def resolve_mode() -> str:
    """Effective filter mode: env override -> mode.json -> off.

    The env var is the explicit per-process override and kill switch (any
    non-empty value that isn't a valid mode reads as an "off" attempt, matching
    the pre-mode-file behavior). The machine-wide switch is mode.json, written
    by the app-launcher toggle (fleet-config#541); absent or malformed degrades
    to "off" — the filter must fail dormant, never fail active.
    """
    env = os.environ.get("FLEET_CONTEXT_FILTER_MODE", "").strip().lower()
    if env:
        return env if env in VALID_MODES else "off"
    try:
        # utf-8-sig: tolerate a BOM if an external .NET/PowerShell writer ever
        # produces the file (same lesson as app-launcher's board_state readers).
        data = json.loads(mode_file_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return "off"
    mode = str(data.get("mode", "") if isinstance(data, dict) else "").strip().lower()
    return mode if mode in VALID_MODES else "off"


def _prune_old_blobs(target_dir: Path) -> None:
    """Best-effort GC: rewrite mode caches one raw blob per wrapped call with a
    unique key, so without a TTL the cache grows forever (fleet-config#541)."""
    cutoff = time.time() - BLOB_TTL_SECONDS
    try:
        entries = list(os.scandir(target_dir))
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
        except OSError:
            continue


def cache_raw_output(command: str, raw: str) -> str:
    """Persist raw output locally and return its content-addressed key."""
    stamp = str(time.time_ns())
    digest = hashlib.sha256((command + "\0" + stamp + "\0" + raw).encode("utf-8", "replace")).hexdigest()
    key = digest[:16]
    target_dir = data_dir() / "blobs"
    target_dir.mkdir(parents=True, exist_ok=True)
    _prune_old_blobs(target_dir)
    (target_dir / f"{key}.txt").write_text(raw, encoding="utf-8", errors="replace")
    return key


# Telemetry log cap: at ~45 MB/year observed growth this triggers roughly every
# five months; one prior generation (shadow.jsonl.1) is kept (fleet-config#549).
SHADOW_LOG_MAX_BYTES = 20 * 1024 * 1024


def _rotate_shadow_log(target: Path) -> None:
    """Best-effort size-based rotation — a telemetry failure must never block
    the wrapped command, so every OSError is swallowed (fleet-config#549)."""
    try:
        if target.stat().st_size < SHADOW_LOG_MAX_BYTES:
            return
    except OSError:
        return
    try:
        target.replace(target.with_name(target.name + ".1"))
    except OSError:
        pass


def append_shadow_log(record: dict[str, Any]) -> None:
    """Append one telemetry row, with the `command` field redacted.

    The row's *output*-derived fields already pass through `SECRET_RE` on the
    compression path, but `command` was written verbatim — so a
    `gh auth login --with-token ghp_…` row landed half-filtered, the secret
    stripped from one field of the record and preserved in the other
    (fleet-config#681). Redacting here rather than at the call site keeps the
    two fields symmetric for every future writer too. The caller's dict is
    copied, never mutated — it is the same object the caller may still read.
    """
    if isinstance(record.get("command"), str):
        record = {**record, "command": redact_secret_markers(record["command"])}
    target = data_dir() / "shadow.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    _rotate_shadow_log(target)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def command_base(command: str) -> str:
    stripped = command.strip()
    if not stripped:
        return ""
    if stripped.startswith("& "):
        stripped = stripped[2:].strip()
    quote = stripped[0] if stripped[0] in {"'", '"'} else ""
    if quote:
        end = stripped.find(quote, 1)
        first = stripped[1:end] if end != -1 else stripped[1:]
    else:
        first = stripped.split(maxsplit=1)[0]
    first = first.replace("\\", "/").rsplit("/", 1)[-1]
    if first.lower().endswith(".exe"):
        first = first[:-4]
    return first.lower()


def is_streaming_or_interactive(command: str) -> bool:
    lower = command.lower()
    if re.search(r"\b(tail|less|more)\b.*\s-[a-z]*f\b", lower):
        return True
    if " --follow" in lower or " --watch" in lower or " -w" in lower:
        return True
    if re.search(r"\bdocker\s+(compose\s+)?up\b", lower) and " -d" not in lower and " --detach" not in lower:
        return True
    if re.search(r"\b(npm|pnpm|yarn|bun)\s+run\s+(dev|start|serve)\b", lower):
        return True
    return False


def split_cd_prefix(command: str) -> tuple[str, str]:
    """Split leading `cd <dir> &&` / `cd <dir>;` segments off `command`.

    Returns `(prefix, body)`, which concatenate back to `command`.
    """
    end = 0
    while True:
        match = CD_PREFIX_RE.match(command, end)
        if not match or match.end() == end:
            return command[:end], command[end:]
        end = match.end()


def effective_command(command: str) -> str:
    """The command that decides the output's shape: cd and env prefixes skipped."""
    body = split_cd_prefix(command.strip())[1]
    match = ENV_PREFIX_RE.match(body)
    return body[match.end():] if match else body


def strip_heredoc_bodies(command: str) -> str:
    """`command` without heredoc bodies, whose text is data, not shell syntax."""
    kept: list[str] = []
    pending: list[str] = []
    for line in command.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        kept.append(line)
        pending.extend(match.group(2) for match in HEREDOC_RE.finditer(line))
    return "\n".join(kept)


def is_compound(command: str) -> bool:
    """True when the output is not one simple command's own (a pipeline, or
    several statements), so no per-command summariser may read it."""
    return bool(COMPOUND_RE.search(strip_heredoc_bodies(effective_command(command)).strip()))


SUPPORTED_COMMANDS = {
    "git", "gh", "pytest", "python", "npm", "pnpm", "yarn", "bun",
    "rg", "grep", "docker", "kubectl", "ruff", "mypy", "tsc",
    "eslint", "go", "cargo", "dotnet", "uv", "pip", "cat", "tail",
}


def rewrite_decision(command: str, tool: str = "") -> RewriteDecision:
    """Whether to wrap `command`, and how to split it (see RewriteDecision).

    `tool` is the shell that will run it (`Bash` / `PowerShell`); empty when
    the output is already captured (Pi's `compress`) and nothing is executed.
    """
    cmd = command.strip()
    if not cmd:
        return RewriteDecision(False, "empty", command)
    if "fleet_context_filter" in cmd or "context_filter_cli.py" in cmd:
        return RewriteDecision(False, "already wrapped", command)
    if SKILL_HELPER_RUN_RE.search(cmd):
        return RewriteDecision(False, "skill helper (its caller reads the payload whole)", command)
    if is_streaming_or_interactive(cmd):
        return RewriteDecision(False, "streaming/interactive", command)
    if re.search(r"\b(git\s+push|npm\s+publish|twine\s+upload|docker\s+push)\b", cmd, re.I):
        return RewriteDecision(False, "publish/destructive", command)

    prefix, body = split_cd_prefix(cmd)
    shape = strip_heredoc_bodies(body)
    if OUTPUT_TO_FILE_RE.search(shape):
        return RewriteDecision(False, "output redirected to a file", command)
    if BACKGROUND_RE.search(shape):
        return RewriteDecision(False, "background job", command)
    if STATE_MUTATION_RE.search(shape):
        return RewriteDecision(False, "shell state mutation", command)
    if tool.lower() == "powershell" and PS7_ONLY_RE.search(shape):
        return RewriteDecision(False, "PowerShell 7 syntax (the wrapper runs 5.1)", command)

    base = command_base(effective_command(body))
    if base in SUPPORTED_COMMANDS:
        return RewriteDecision(True, "supported command", command, prefix=prefix, body=body)
    return RewriteDecision(False, f"unsupported command: {base or '<unknown>'}", command)


def redact_secret_markers(text: str) -> str:
    return SECRET_RE.sub("[REDACTED_SECRET]", text)


def _collapse_identical_runs(lines: Iterable[str]) -> list[str]:
    """Collapse runs of *byte-identical* adjacent lines into one ``[xN]`` row.

    Lines that differ in any way are never merged (fleet-config#837). The former
    implementation keyed each run on a *template* of the line with timestamps,
    uuids, hex digests, paths and numbers masked out — so two distinct worktree
    paths rendered as a single ``[x2]`` row and the second path was simply gone,
    with the count reading like corroboration. A count is not a substitute for
    the values when the values are the payload, and every category that template
    masked — a path, an id, a number — is exactly the kind of value that is the
    payload. Collapsing identical lines stays lossless, so it stays.
    """
    collapsed: list[str] = []
    last_line: Optional[str] = None
    last_key = ""
    count = 0

    def flush() -> None:
        if count <= 0 or last_line is None:
            return
        collapsed.append(last_line if count == 1 else f"[x{count}] {last_line}")

    for line in lines:
        key = line.rstrip()
        if last_line is not None and key == last_key:
            count += 1
            continue
        flush()
        last_line = line
        last_key = key
        count = 1
    flush()
    return collapsed


def _dedupe_exact(lines: Iterable[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for line in lines:
        key = line.strip()
        if not key:
            out.append(line)
            continue
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= 2 or SIGNAL_RE.search(line):
            out.append(line)
    suppressed = sum(count - 2 for count in seen.values() if count > 2)
    if suppressed:
        out.append(f"[fleet-context-filter: suppressed {suppressed} repeated lines]")
    return out


def _git_status(lines: list[str]) -> Optional[list[str]]:
    interesting: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("On branch", "Your branch", "Changes ", "Untracked ", "Changes not")):
            interesting.append(stripped)
        elif re.match(r"(modified|new file|deleted|renamed|both modified):", stripped):
            interesting.append(stripped)
        elif re.match(r"[ MADRCU?!]{1,2}\s+\S+", line):
            interesting.append(stripped)
    return interesting or None


PYTEST_FAILURE_HEADER_RE = re.compile(r"^_{4,}\s*\S")


def _pytest(lines: list[str]) -> Optional[list[str]]:
    """RTK-shaped pytest summary: per failure keep the first error line + every
    ``file:line`` anchor, plus the short summary and counts. The lossy part is
    dropping the ``E   +  where/and`` continuation explosion and captured
    stdout/stderr noise that dominate a many-failure run without adding signal.
    """
    keep: list[str] = []
    seen_error_line = False
    for line in lines:
        stripped = line.rstrip()
        # A new ``____ test_name ____`` block resets the first-error tracking so
        # each failure contributes its own summary line.
        if PYTEST_FAILURE_HEADER_RE.match(stripped.strip()):
            seen_error_line = False
            continue
        if (
            " short test summary info " in stripped
            or stripped.startswith(("FAILED ", "ERROR "))
            or re.search(r"\b\d+\s+(failed|passed|error|errors|skipped)\b", stripped)
            or "Traceback (most recent call last)" in stripped
            or re.search(r"\b[A-Za-z0-9_./\\-]+\.py:\d+:", stripped)
        ):
            keep.append(stripped)
            continue
        # Keep only the first ``E   ``/``F   `` line of each failure block — the
        # assertion/error message itself — and drop its ``+  where/and`` tail.
        if stripped.startswith(("E   ", "F   ")):
            if not seen_error_line:
                keep.append(stripped)
                seen_error_line = True
            continue
    return keep or None


def _npm(lines: list[str]) -> Optional[list[str]]:
    keep: list[str] = []
    pass_count = 0
    has_failure = False
    for line in lines:
        stripped = line.rstrip()
        if re.search(r"\bPASS\s+\S+", stripped):
            pass_count += 1
        if re.search(r"\bFAIL\s+\S+", stripped) or SIGNAL_RE.search(stripped):
            has_failure = True
        if SIGNAL_RE.search(stripped) or re.search(r"\b(Test Suites|Tests|Snapshots|Time):", stripped):
            keep.append(stripped)
        elif re.search(r"\b(PASS|FAIL)\s+\S+", stripped):
            keep.append(stripped)
    if pass_count and not has_failure:
        summary = [f"all visible test suites passed ({pass_count} PASS lines)"]
        summary.extend(line for line in keep if re.search(r"\b(Test Suites|Tests|Snapshots|Time):", line))
        return summary
    return keep or None


def _git_log(lines: list[str]) -> Optional[list[str]]:
    """Head-cap a long history: keep the most-recent commits verbatim (SHAs and
    subjects intact — never templated away) and trim only the older tail the
    agent did not scroll to. Signal-preserving by construction; the win comes
    entirely from dropping old history on an unbounded ``git log``.
    """
    nonblank = [line.rstrip() for line in lines if line.strip()]
    if len(nonblank) <= GIT_LOG_HEAD_LINES:
        return None
    omitted = len(nonblank) - GIT_LOG_HEAD_LINES
    return nonblank[:GIT_LOG_HEAD_LINES] + [
        f"[fleet-context-filter: {omitted} earlier commits omitted]"
    ]


def command_specific_lines(command: str, lines: list[str]) -> Optional[list[str]]:
    base = command_base(command)
    lower = command.lower()
    if base == "git" and " status" in f" {lower} ":
        return _git_status(lines)
    if base == "git" and " log" in f" {lower} " and ("--oneline" in lower or "format:oneline" in lower):
        return _git_log(lines)
    if base == "pytest" or " pytest" in f" {lower} ":
        return _pytest(lines)
    if base in {"npm", "pnpm", "yarn", "bun"} and re.search(r"\b(test|run\s+test)\b", lower):
        return _npm(lines)
    # `cat`/`tail` deliberately have no branch here — see CONTENT_COMMANDS.
    return None


def _is_json(text: str) -> bool:
    stripped = text.strip()
    if stripped[:1] not in {"{", "["}:
        return False
    try:
        json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return True


def _verbatim(safe_raw: str, total_lines: int, max_chars: int) -> str:
    """Return content output unsummarised, byte-truncated at the budget.

    Truncation is announced with the numbers that make the loss legible and
    bounded, so it can never be read as "that was the whole file"
    (fleet-config#837). Characters as well as lines: one long line cut at the
    budget is "first 1 of 1 lines", which alone says nothing was withheld
    (fleet-config#1269).
    """
    body = safe_raw.rstrip("\n")
    if len(body) <= max_chars:
        return body
    head = body[:max_chars].rstrip()
    kept = len(head.splitlines())
    return head + (
        f"\n[fleet-context-filter: VERBATIM HEAD — first {len(head)} of {len(body)} chars, "
        f"{kept} of {total_lines} lines; the rest withheld past the {max_chars}-char budget]"
    )


def compress_output(
    command: str,
    raw: str,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    max_chars: int = DEFAULT_MAX_CHARS,
    cache_raw: bool = False,
) -> CompressionResult:
    start = time.perf_counter()
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    secret_like = bool(SECRET_RE.search(normalized))
    safe_raw = redact_secret_markers(normalized)
    raw_tokens = estimate_tokens(normalized)
    lines = safe_raw.splitlines()
    shaping = effective_command(command)

    # Compound output (a pipeline's last stage, or several statements) is
    # returned like content (fleet-config#1268): `git status && git log` read
    # through the status summariser would drop every log line.
    if command_base(shaping) in CONTENT_COMMANDS or is_compound(command) or _is_json(safe_raw):
        compressed = _verbatim(safe_raw, len(lines), max_chars)
    else:
        if len(safe_raw) <= SMALL_OUTPUT_CHARS and len(lines) <= max_lines:
            candidate_lines = lines
        else:
            candidate_lines = command_specific_lines(shaping, lines) or []
            if not candidate_lines:
                signal = [line.rstrip() for line in lines if SIGNAL_RE.search(line)]
                head = [line.rstrip() for line in lines[: min(12, len(lines))]]
                tail = [line.rstrip() for line in lines[-min(20, len(lines)) :]] if len(lines) > 12 else []
                candidate_lines = head + ["[... middle omitted ...]"] + signal + tail

        candidate_lines = _collapse_identical_runs(_dedupe_exact(candidate_lines))
        if len(candidate_lines) > max_lines:
            tail_budget = max(8, max_lines // 4)
            candidate_lines = (
                candidate_lines[: max_lines - tail_budget - 1]
                + [f"[fleet-context-filter: omitted {len(candidate_lines) - max_lines + 1} low-signal lines]"]
                + candidate_lines[-tail_budget:]
            )

        compressed = "\n".join(candidate_lines).strip()
        if len(compressed) > max_chars:
            compressed = compressed[:max_chars].rstrip() + "\n[fleet-context-filter: truncated at char budget]"

    if not compressed:
        compressed = "[fleet-context-filter: command produced no output]"

    compressed_tokens = estimate_tokens(compressed)
    if compressed_tokens > raw_tokens:
        compressed = safe_raw
        compressed_tokens = estimate_tokens(compressed)

    withheld = compressed.strip() != safe_raw.strip()
    # Only output with something withheld has anything to retrieve; most
    # wrapped calls come back whole (fleet-config#1268).
    raw_key = None
    if cache_raw and withheld and not secret_like and normalized:
        raw_key = cache_raw_output(command, normalized)

    duration_ms = (time.perf_counter() - start) * 1000
    reduction = 0.0 if raw_tokens == 0 else (raw_tokens - compressed_tokens) / raw_tokens * 100
    return CompressionResult(
        command=command,
        raw=normalized,
        compressed=compressed,
        raw_tokens=raw_tokens,
        compressed_tokens=compressed_tokens,
        reduction_pct=reduction,
        line_count=len(lines),
        compressed_line_count=len(compressed.splitlines()),
        duration_ms=duration_ms,
        raw_key=raw_key,
        secret_like=secret_like,
        withheld=withheld,
    )
