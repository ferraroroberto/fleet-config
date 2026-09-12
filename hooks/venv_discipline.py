"""Enforce the project's `.venv` discipline.

Every project in the fleet uses `.venv` (never `venv`), never activates,
always invokes via `& .\\.venv\\Scripts\\python.exe ...`. This hook catches
the five common drifts:

  1. `python -m venv venv`   — wrong directory name
  2. `.\\.venv\\Scripts\\activate` / `source .venv/bin/activate`
                              — activation is banned
  3. Bare `python <file>` / `pip install ...` when a project `.venv` exists
                              — would hit the system Python instead
  4. A recursive delete, or a venv (re)creation, whose target resolves through
     a **`.venv` junction** — fleet-config#847
  5. A recursive delete aimed straight at a **primary checkout's real `.venv`**
     — fleet-config#828; `git clean -x|-X` counts as one (fleet-config#867)

Rules 4 and 5 are the destructive ones, and they are the same loss by two
routes. A worktree's `.venv` is a *junction* to the primary checkout's real
venv (`worktree_claim.py`: worktrees don't share untracked files, and a 24-repo
fleet can't recreate heavy venvs per worktree), so `git worktree remove`,
`rm -rf`, `Remove-Item -Recurse` and `rmdir /s` all follow the reparse point
and delete the primary's real site-packages — which every other lane in that
repo, and any running app, is importing from. Only `worktree_claim.py
remove-worktree` is safe: it strips the junction with a bare `rmdir`
(reparse-safe, never `/s`) *before* calling git.

Rule 5 closes the other route. `email-archiver`'s venv was emptied during an
unattended `/cleanup-fleet-all` run and the evidence could not distinguish a
delete that walked *through* a junction from one aimed at the primary's own
`.venv` (fleet-config#828), because rule 4 answers only the first. Both end
identically: `.venv` is machine-local, gitignored and **not recoverable from
git**, so the only repair is recreate-and-reinstall by hand. A worktree's *own*
real (non-junctioned) `.venv` is deliberately not covered — that one is the
worktree's to dispose of, and `git worktree remove` is entitled to it.

Every clause either rule classifies as destructive is appended to the
`destructive-actions.jsonl` audit trail under `state_dir()`, blocked or not.
That file is the answer to #828's third acceptance criterion: the run that
emptied the venv retained only per-sub-agent progress markers, so the command
was never identifiable at all. A refusal only the agent sees is not a record.

What the trail deliberately does *not* cover (fleet-config#867, decided): a
delete inside a script — `python tidy.py` calling `shutil.rmtree` — shows the
hook no verb and no target. Recording every out-of-repo `python <script>` was
the cheap partial step on offer, and was rejected: it would flood the trail
with the scratch probes agents run all day, still miss `python -c`, executed
heredocs, in-repo scripts and anything the script spawns, and each record
would name a script, never the path it deleted — so it answers neither "was
it stopped" nor "what went". Closing that gap needs process-level evidence
(filesystem auditing on `.venv`), not a command-string matcher.

Allow-listed:
  * `python -m venv .venv`                 — correct directory name
  * `python -m venv --clear .venv`         — flags are flags, not a directory name
  * `& .\\.venv\\Scripts\\python.exe ...`  — correct invocation form
  * `& ./.venv/bin/python ...`             — POSIX equivalent
  * Bare `python` when no `.venv` exists at or above the project root.
  * `rmdir <path>` with no `/s` — the reparse-safe form, which by definition
    cannot recurse into a junction's target.
  * `pip install` / `pip uninstall` through a worktree's junctioned venv —
    the venv is *deliberately* shared, so mutating packages through it is the
    intended behaviour, not the footgun. Rule 4 is about delete and recreate.
  * `python -m venv --clear .venv` against a *real* `.venv` — the sanctioned
    in-place rebuild of a corrupt venv, and the escape hatch rule 5 points at.
  * A recursive delete of a directory that holds no `.venv` at all, and of a
    linked worktree's own real `.venv`.

Heredoc bodies that the consuming command can only *write* — `cat`/`tee` into
a file, or `--body-file -` — are stripped before any matching: that text is
documentation, not a command. This guard refused the very issue that asked for
rule 4 to exist, because the issue body quoted the hazard it describes
(fleet-config#847). Bodies that are executed (`python - <<'PY'`, `cat <<'EOF'
| sh`) are left intact, redirect or no redirect.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402


# 1) `python -m venv <name>` where name is NOT `.venv`.
#
# `venv`'s own flags (`--clear`, `--upgrade`, `--system-site-packages`, …) sit
# between the module and the directory, and are NOT a directory name: the
# original pattern read the first token after `venv` unconditionally and
# refused `python -m venv --clear .venv` as if `--clear` were a bad name
# (fleet-config#847). Flags are consumed, and the captured name may not itself
# start with `-`, so a flags-only fragment matches nothing at all.
#
# The `.exe` in the invocation prefix matters for rule 4: the fleet's own
# documented form is `& .\.venv\Scripts\python.exe -m venv …`, and without it
# only a bare `python` reached either pattern.
_VENV_INVOKE = r"\bpython\w*(?:\.exe)?\s+(?:-\d(?:\.\d+)?\s+)?-m\s+venv\s+"
_VENV_FLAG = r"(?:--?[\w-]+(?:=[^\s;|&]+)?\s+)*"
_VENV_NAME = r"([^-\s;|&][^\s;|&]*)"

VENV_CREATE_RE = re.compile(
    _VENV_INVOKE + _VENV_FLAG + r"(?!\.venv\b)" + _VENV_NAME,
    re.IGNORECASE,
)

# Same shape, but capturing the target of *any* venv creation — including the
# correctly-named `.venv` — so rule 4 can ask whether that target is a junction.
VENV_TARGET_RE = re.compile(
    _VENV_INVOKE + _VENV_FLAG + _VENV_NAME,
    re.IGNORECASE,
)

# 2) Activation
ACTIVATE_PATTERNS = (
    r"\.[\\/]\.venv[\\/]Scripts[\\/]Activate(?:\.ps1|\.bat)?\b",
    r"\bsource\s+[^\s;|&]*\.venv/bin/activate\b",
    r"\bvenv[\\/]Scripts[\\/]Activate(?:\.ps1|\.bat)?\b",
    r"\bsource\s+[^\s;|&]*venv/bin/activate\b",
)

# 3) Bare `python` / `pip` invocation. We only block when:
#      (a) the token appears at a command boundary (start of line, or after
#          `;`/`&&`/`||`/`|`/`& `), NOT deep inside a quoted string;
#      (b) a project `.venv` exists at or above cwd.
#
# Matches `python ...`, `python3 ...`, `pip install ...`. Does NOT match
# `& .\.venv\Scripts\python.exe ...` (path-scoped) or `py -m ...` (the
# launcher is fine for one-off tooling like py_compile).
_BOUNDARY = r"(?:^|[\r\n;]|&&|\|\||\||&\s)\s*"
BARE_PYTHON_RE = re.compile(
    _BOUNDARY + r"python\d*(?:\.exe)?(?=\s|$)",
    re.IGNORECASE,
)
BARE_PIP_RE = re.compile(
    _BOUNDARY + r"pip\d*(?:\.exe)?(?=\s|$)",
    re.IGNORECASE,
)

# These prefixes mean the command is path-scoped — allow.
PATH_SCOPED_HINTS = (
    r"\.venv[\\/]Scripts[\\/]python",
    r"\.venv/bin/python",
    r"\.venv[\\/]Scripts[\\/]pip",
    r"\.venv/bin/pip",
)


def _is_path_scoped(cmd: str) -> bool:
    return any(re.search(p, cmd, re.IGNORECASE) for p in PATH_SCOPED_HINTS)


# Same clause separators safe_kill_guard.py's forced_push_refspecs already
# splits on. Path-scoping must be judged per clause, not over the whole
# command string — otherwise a compound command that pairs a correctly
# venv-scoped invocation with a later bare `python`/`pip` clause passes
# whole, because `_is_path_scoped` is satisfied by the first clause
# (fleet-config#709).
_SEGMENT_SPLIT_RE = re.compile(r"[\n;|&]+")


# --------------------------------------------------------- heredoc stripping
#
# `cat > notes.md <<'EOF' … EOF` writes a file; its body is text, never a
# command. Matching guard patterns inside it produces a refusal for *writing
# about* the hazard — which is how this hook blocked fleet-config#847 from
# being filed, on the phrase the issue was quoting.
#
# The discriminator is the *consuming command*, not the redirect alone: a body
# is stripped only where that command cannot execute it. `cat`/`tee` into a
# file, and `--body-file -` (how the fleet files issues and PRs), are text
# sinks. `python - <<'PY' > out.txt` is NOT — it has a file redirect but runs
# its body — and `cat <<'EOF' | sh` is not either, which is why the redirect is
# still required alongside `cat`.

_HEREDOC_START_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# A `>` / `>>` that redirects to a path — not `2>&1`, not the `<<` itself.
_FILE_REDIRECT_RE = re.compile(r"(?<![0-9<>])>>?\s*(?!&)")
# Commands that copy stdin to a file or to stdout without ever running it.
_TEXT_SINK_COMMANDS = {"cat", "tee"}
# `gh issue create --body-file -`, `gh pr create -F -`: the body becomes an API
# field, never a command.
_BODY_FILE_STDIN_RE = re.compile(r"(?:--body-file|--body|-F)[=\s]+-(?=\s|$)")


def _is_text_sink_line(line: str) -> bool:
    """True when this heredoc's body is written somewhere, never executed."""
    if _BODY_FILE_STDIN_RE.search(line):
        return True
    tokens = _tokens(line)
    if not tokens:
        return False
    first = tokens[0].lower().rsplit("/", 1)[-1]
    return first in _TEXT_SINK_COMMANDS and bool(_FILE_REDIRECT_RE.search(line))


def strip_nonexecuted_heredoc_bodies(cmd: str) -> str:
    """`cmd` with the body of every text-sink heredoc removed.

    Delimiter lines are kept so line-oriented patterns can't be knitted
    together across the hole the body leaves behind.
    """
    lines = cmd.split("\n")
    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        match = _HEREDOC_START_RE.search(line)
        if match and _is_text_sink_line(line):
            delimiter = match.group(2)
            i += 1
            while i < len(lines) and lines[i].strip() != delimiter:
                i += 1
            if i < len(lines):
                out.append(lines[i])  # the closing delimiter
        i += 1
    return "\n".join(out)


# ------------------------------------------------------ .venv junction guard

_TOKEN_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"|(\S+)")


def _tokens(segment: str) -> List[str]:
    """Shell-ish tokens, quotes stripped, backslashes left alone.

    `shlex` is not usable here: POSIX mode eats the backslashes out of every
    Windows path, and non-POSIX mode keeps the quotes.
    """
    out: List[str] = []
    for match in _TOKEN_RE.finditer(segment):
        for group in match.groups():
            if group is not None:
                out.append(group)
                break
    return out


# A cmd.exe switch (`/s`, `/q`) vs. a Git Bash absolute path (`/e/automation/…`)
# — both start with `/`, and treating the second as a flag would drop the
# operand from the exact command that caused fleet-config#847.
_CMD_SWITCH_RE = re.compile(r"^/[A-Za-z]{1,3}$")

# Git Bash hands us MSYS paths; `Path("/e/automation/x")` has a root but no
# drive on Windows, so it is not `is_absolute()` and would be joined onto the
# payload cwd. Translate it back to the drive form first.
_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])/(.*)$")


# PowerShell resolves any unambiguous prefix of a parameter name, so `-r`,
# `-rec` and `-Recurse` are all the same switch.
_PS_RECURSE_RE = re.compile(r"^-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?$", re.IGNORECASE)


def _is_flag(token: str) -> bool:
    return token.startswith("-") or bool(_CMD_SWITCH_RE.match(token))


def _operand_path(raw: str, base: Path) -> Path:
    """`raw` as a filesystem path to test, resolved against the payload cwd.

    A glob is reduced to the directory it expands *within*: the shell expands
    `rm -rf *` long after the hook has seen the command string, so the only
    honest question left is whether that directory holds the junction.
    """
    text = raw.strip().strip("'\"")
    msys = _MSYS_DRIVE_RE.match(text)
    if msys:
        text = f"{msys.group(1).upper()}:/{msys.group(2)}"
    if any(ch in text for ch in "*?["):
        text = text.replace("\\", "/").rsplit("/", 1)[0] if "/" in text.replace("\\", "/") else "."
    path = Path(text)
    if not path.is_absolute():
        path = base / path
    return path


def is_reparse_point(path: Path) -> bool:
    """True if `path` is a junction, symlink, or other reparse point.

    Windows junctions set `FILE_ATTRIBUTE_REPARSE_POINT`; the `S_ISLNK`
    fallback covers POSIX symlinks, which is also what makes this testable off
    Windows.
    """
    try:
        st = os.lstat(path)
    except (OSError, ValueError):
        return False
    attributes = getattr(st, "st_file_attributes", 0)
    if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return True
    return stat.S_ISLNK(st.st_mode)


def junctioned_venv_under(target: Path) -> Optional[Path]:
    """The `.venv` reparse point a delete of `target` would recurse *through*.

    Two shapes reach the primary's real venv: the junction named directly
    (`rm -rf <wt>/.venv`), and the worktree that contains it (`git worktree
    remove <wt>`). Returns None when there is no junction — a real `.venv`
    directory is the caller's own to delete.
    """
    if target.name == ".venv" and is_reparse_point(target):
        return target
    nested = target / ".venv"
    if is_reparse_point(nested):
        return nested
    return None


def is_primary_checkout_dir(path: Path) -> bool:
    """True when `path` is a repo's *main* working tree.

    A linked worktree's `.git` is a **file** — a gitfile pointing at
    `<primary>/.git/worktrees/<name>`; a primary checkout's is a directory.
    That single distinction is the whole test, and reading it costs one stat
    rather than a `git rev-parse` subprocess per operand.

    Deliberately *not* named `is_primary_checkout`: `worktree_claim.py` owns a
    function by that name which answers the same question from git itself
    (`--git-common-dir` == `--git-dir`). A hook may not import `skills/_lib`
    (the `tree_boundary` acceptance check enforces it), so this tier needs its
    own answer — but the two are a stat-cheap and a git-exact reading of one
    property, not a duplicate, and the names must not suggest otherwise.
    """
    try:
        return (path / ".git").is_dir()
    except OSError:
        return False


def primary_venv_under(target: Path) -> Optional[Path]:
    """The primary checkout's real `.venv` a delete of `target` would destroy.

    Two shapes, mirroring `junctioned_venv_under`: the venv named directly
    (`<repo>/.venv`) and the checkout that holds it (`<repo>`). Returns None
    for a reparse point — that is rule 4's, checked first — and None for a
    linked worktree's own real `.venv`, which is the worktree's to dispose of.
    """
    if (target.name == ".venv" and target.is_dir()
            and not is_reparse_point(target) and is_primary_checkout_dir(target.parent)):
        return target
    nested = target / ".venv"
    if (nested.is_dir() and not is_reparse_point(nested)
            and is_primary_checkout_dir(target)):
        return nested
    return None


# The one non-delete verb `destructive_operands` reports. Rule 4 cares about it
# — building over a junction writes into somebody else's venv — but rule 5 must
# not: `python -m venv --clear .venv` against a repo's *own* real venv is the
# sanctioned in-place rebuild, and the escape hatch rule 5's own refusal points
# at. A guard that refuses its own remedy is the expensive kind of wrong.
VENV_BUILD_VERB = "python -m venv"

# `git clean` with the ignore rules off (fleet-config#867). `-x` drops them and
# `-X` deletes *only* ignored paths, and every fleet repo ignores `.venv`, so in
# a primary checkout either one deletes the real venv outright. Rule 4 must not
# answer it: inside a worktree git unlinks the `.venv` junction without
# following it (reproduced in the acceptance check), so a "guts the primary"
# refusal there would be false.
GIT_CLEAN_VERB = "git clean -x"
# The same clean told to keep `.venv` (`-x -e .venv`) — still a delete worth a
# record, never a venv at risk. `-e` protects only under `-x`: with `-X` it
# *adds* an ignore rule, and ignored paths are exactly what `-X` removes.
GIT_CLEAN_SPARING_VERB = "git clean -x -e .venv"

# `git` global options that consume the next token (`-C <path>`, `-c <k=v>`).
_GIT_GLOBAL_WITH_ARG = {"-c", "--git-dir", "--work-tree", "--namespace",
                        "--config-env", "--super-prefix"}
# `2>&1`, `>out.txt`, `2>` + a following file: redirections, never pathspecs.
# A missed one would be read as a pathspec and silently narrow the operand away
# from the checkout root — an allow on the exact command this rule exists for.
_REDIRECT_RE = re.compile(r"^\d*(?:>>?|<)(&\d*)?")
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _join_operand(base: str, raw: str) -> str:
    """`raw` against a `git -C` directory, kept as a string.

    Joined with `/` rather than `Path`: an MSYS `-C /e/automation/x` must reach
    `_operand_path` still MSYS-shaped, or its drive translation never fires.
    """
    if not base or re.match(r"^(?:[A-Za-z]:)?[\\/]", raw):
        return raw
    return base.rstrip("\\/") + "/" + raw


def git_clean_operands(tokens: List[str]) -> Tuple[Optional[str], List[str]]:
    """`(verb, paths)` for a `git [globals] clean` that can reach a `.venv`.

    `(None, [])` for anything else — including a dry run (`-n`), a clean that
    keeps the ignore rules (no `-x`/`-X`), and `-x` with neither `-d` nor a
    pathspec, which never descends into an untracked directory. With a
    pathspec, `-d` is irrelevant: `git clean -xf .` deletes `.venv` too. With
    none, the operand is the directory git runs in (cwd, or `-C`), since git
    cleans only below it.
    """
    i = 0
    while i < len(tokens) and _ENV_ASSIGN_RE.match(tokens[i]):
        i += 1
    if i >= len(tokens) or tokens[i].lower().replace("\\", "/").rsplit("/", 1)[-1] \
            not in {"git", "git.exe"}:
        return None, []
    i += 1
    workdir = ""
    while i < len(tokens) and tokens[i].startswith("-"):
        option = tokens[i]
        if option == "-C" and i + 1 < len(tokens):
            workdir = _join_operand(workdir, tokens[i + 1])
            i += 2
        elif option.startswith("--work-tree="):
            workdir = _join_operand(workdir, option.split("=", 1)[1])
            i += 1
        elif option in _GIT_GLOBAL_WITH_ARG:
            if option == "--work-tree" and i + 1 < len(tokens):
                workdir = _join_operand(workdir, tokens[i + 1])
            i += 2
        else:
            i += 1
    if i >= len(tokens) or tokens[i] != "clean":
        return None, []

    flags = set()
    excludes: List[str] = []
    pathspecs: List[str] = []
    rest = tokens[i + 1:]
    j = 0
    options_done = False
    while j < len(rest):
        token = rest[j]
        j += 1
        redirect = _REDIRECT_RE.match(token)
        if redirect:
            # A bare operator (`>`, `2>`) takes the next token as its file.
            if redirect.end() == len(token) and not redirect.group(1):
                j += 1
            continue
        if options_done or not token.startswith("-") or token == "-":
            pathspecs.append(token)
        elif token == "--":
            options_done = True
        elif token.startswith("--"):
            # git accepts any unambiguous prefix of a long option (`--dry`).
            name, _, value = token.partition("=")
            if "--exclude".startswith(name):
                if not value and j < len(rest):
                    value, j = rest[j], j + 1
                excludes.append(value)
            elif "--dry-run".startswith(name):
                flags.add("n")
        else:
            cluster = token[1:]
            for k, ch in enumerate(cluster):
                if ch == "e":
                    value = cluster[k + 1:]
                    if not value and j < len(rest):
                        value, j = rest[j], j + 1
                    excludes.append(value)
                    break
                flags.add(ch)

    if "n" in flags or not flags & {"x", "X"}:
        return None, []
    if "d" not in flags and not pathspecs:
        return None, []
    spares_venv = "X" not in flags and any(
        e.strip("\\/") == ".venv" for e in excludes)
    verb = GIT_CLEAN_SPARING_VERB if spares_venv else GIT_CLEAN_VERB
    return verb, [_join_operand(workdir, p) for p in pathspecs] or [workdir or "."]


def destructive_operands(segment: str) -> Tuple[Optional[str], List[str]]:
    """`(verb, paths)` for a segment that deletes recursively or builds a venv.

    `(None, [])` when the segment does neither. `rmdir` without `/s` is
    deliberately absent: it removes a reparse point without following it, and
    is the safe form `worktree_claim.py` itself uses.
    """
    tokens = _tokens(segment)
    if not tokens:
        return None, []
    lowered = [t.lower() for t in tokens]

    # `git [-C <repo>] clean -x|-X …` — its own parser: cluster flags, `-e`
    # arguments, pathspecs and redirections all change the answer.
    verb, operands = git_clean_operands(tokens)
    if verb:
        return verb, operands

    # `git [-C <repo>] worktree remove [--force] <path>`
    if "git" in lowered and "worktree" in lowered:
        index = lowered.index("worktree")
        if index + 1 < len(lowered) and lowered[index + 1] == "remove":
            return "git worktree remove", [
                t for t in tokens[index + 2:] if not _is_flag(t)
            ]

    # `rm -rf <path>` (a recursive flag anywhere in the flag run)
    if lowered[0] in {"rm", "rm.exe"}:
        recursive = any(
            t == "--recursive" or (t.startswith("-") and not t.startswith("--") and ("r" in t or "R" in t))
            for t in tokens[1:]
        )
        if recursive:
            return "rm -r", [t for t in tokens[1:] if not _is_flag(t)]

    # `Remove-Item -Recurse <path>` (PowerShell). PowerShell accepts any
    # unambiguous prefix of a parameter name, so `-r` and `-rec` are as legal
    # as `-Recurse` and must match too.
    if lowered[0] in {"remove-item", "ri", "rm", "del", "erase"}:
        if any(_PS_RECURSE_RE.match(t) for t in tokens[1:]):
            return "Remove-Item -Recurse", [t for t in tokens[1:] if not _is_flag(t)]

    # `rmdir /s <path>` / `del /s <path>` (cmd). Without `/s` both are safe:
    # `rmdir` removes a reparse point without following it, and `del` needs
    # `/s` before it will descend at all.
    if lowered[0] in {"rmdir", "rd", "del", "erase"}:
        if any(t.lower().lstrip("/").find("s") >= 0 and t.startswith("/") for t in tokens[1:]):
            return lowered[0] + " /s", [t for t in tokens[1:] if not _is_flag(t)]

    # `python -m venv [flags] <dir>` — creating or `--clear`-ing over a junction
    # writes through it just as surely as a delete does.
    match = VENV_TARGET_RE.search(segment)
    if match:
        return VENV_BUILD_VERB, [match.group(1)]

    return None, []


class Clause(NamedTuple):
    """One destructive clause, with the venv it would take out (if any).

    `rule` is `"junction"` when the delete would follow a worktree's `.venv`
    reparse point into the primary's real venv, `"primary"` when it is aimed at
    a primary checkout's real venv directly, and `None` when it destroys
    neither. A `None` clause is still a clause: it is recorded, never blocked.
    """

    rule: Optional[str]
    verb: str
    operand: str
    venv: Optional[Path]


def destructive_clauses(cmd: str, base: Path) -> List[Clause]:
    """Every recursive-delete / venv-rebuild clause in `cmd`, in order.

    Both hazard routes are judged here rather than in two parallel scanners:
    they share the clause split, the operand resolution and the audit record,
    and differ only in which predicate answers. Junction first — a worktree's
    `.venv` is a reparse point, so rule 5's `is_dir()` would also be true of
    its target and would mislabel the more specific case.
    """
    out: List[Clause] = []
    for segment in _SEGMENT_SPLIT_RE.split(cmd):
        verb, operands = destructive_operands(segment)
        if not verb:
            continue
        if not operands:
            out.append(Clause(None, verb, "", None))
            continue
        for operand in operands:
            if verb == GIT_CLEAN_SPARING_VERB:
                out.append(Clause(None, verb, operand, None))
                continue
            path = _operand_path(operand, base)
            junction = (None if verb == GIT_CLEAN_VERB
                        else junctioned_venv_under(path))
            if junction is not None:
                out.append(Clause("junction", verb, operand, junction))
                continue
            primary = (None if verb == VENV_BUILD_VERB
                       else primary_venv_under(path))
            out.append(Clause("primary" if primary else None, verb, operand, primary))
    return out


# ------------------------------------------------- destructive-action audit

AUDIT_FILENAME = "destructive-actions.jsonl"
# One rotation, not a rolling set: this is a forensic tail read after an
# incident, not a metrics feed. Roughly 10k records at the excerpt limit below.
AUDIT_MAX_BYTES = 4_000_000
COMMAND_EXCERPT_LIMIT = 2000


def audit_path() -> Path:
    """Resolved at call time so `CLAUDE_HOOKS_STATE_DIR` always wins, exactly
    as every other hook's state file does."""
    return _lib.state_dir() / AUDIT_FILENAME


def audit_record(payload: Dict[str, Any], cmd: str, clause: Clause,
                 verdict: str) -> Dict[str, Any]:
    """The JSON object one destructive clause contributes to the trail."""
    return {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z"),
        "verdict": verdict,
        "rule": clause.rule or "",
        "verb": clause.verb,
        "operand": clause.operand,
        "venv": str(clause.venv) if clause.venv else "",
        "tool": _lib.tool_name(payload) or "",
        "cwd": str(_lib.cwd(payload)),
        "launcher_session": _lib.launcher_session_id(),
        "session": str(payload.get("session_id") or ""),
        "command": cmd[:COMMAND_EXCERPT_LIMIT],
    }


def record_destructive_action(payload: Dict[str, Any], cmd: str,
                              clause: Clause, verdict: str) -> None:
    """Append one audit line. Never raises, never blocks the hook.

    fleet-config#828: a `/cleanup-fleet-all` lane emptied a primary checkout's
    `.venv` and the command was never identified, because the only retained
    artifact of that run was a per-sub-agent progress marker — no transcript,
    no commands. `ALLOW` records matter as much as `BLOCK` ones: a refusal the
    agent alone sees answers "was it stopped", never "what ran".

    Best-effort by construction. A guard that failed closed on an unwritable
    state directory would convert an audit gap into a fleet-wide outage of
    every Bash call, which is a strictly worse failure than the one it records.
    """
    try:
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size > AUDIT_MAX_BYTES:
                os.replace(path, path.with_suffix(path.suffix + ".1"))
        except OSError:
            pass
        line = json.dumps(audit_record(payload, cmd, clause, verdict),
                          ensure_ascii=False)
        # One `open(..., "a")` + one write: O_APPEND makes a single short line
        # atomic against the concurrent sessions this file is shared by.
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 - see docstring
        pass


def main() -> None:
    payload = _lib.read_stdin_json()
    if _lib.tool_name(payload) not in {"Bash", "PowerShell"}:
        _lib.allow()

    cmd = _lib.command_string(payload)
    if not cmd:
        _lib.allow()

    # Documentation about a hazard is not the hazard (fleet-config#847).
    cmd = strip_nonexecuted_heredoc_bodies(cmd)

    # 1) Wrong-name venv creation
    m = VENV_CREATE_RE.search(cmd)
    if m:
        bad_name = m.group(1).strip().strip("'\"")
        _lib.block(
            "Blocked: `python -m venv " + bad_name + "` — the canonical "
            "directory name in this fleet is `.venv` (with a leading dot). "
            "Use `python -m venv .venv` instead."
        )

    # 2) Activation
    for pattern in ACTIVATE_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            _lib.block(
                "Blocked: never activate the venv (matched: " + pattern + "). "
                "Invoke directly: `& .\\.venv\\Scripts\\python.exe ...` on Windows, "
                "`./.venv/bin/python ...` on POSIX."
            )

    # 3) Bare python/pip when a project .venv is present. Evaluated per
    # clause (see _SEGMENT_SPLIT_RE) so a compound command can't launder a
    # bare invocation behind an earlier, correctly-scoped one.
    hit_verb = None
    for segment in _SEGMENT_SPLIT_RE.split(cmd):
        has_bare_python = bool(BARE_PYTHON_RE.search(segment))
        has_bare_pip    = bool(BARE_PIP_RE.search(segment))
        if (has_bare_python or has_bare_pip) and not _is_path_scoped(segment):
            hit_verb = "python" if has_bare_python else "pip"
            break

    if hit_verb is not None:
        venv_python = _lib.find_venv_python(_lib.cwd(payload))
        if venv_python is not None:
            _lib.block(
                "Blocked: bare `" + hit_verb + "` invocation with a project .venv present at "
                + str(venv_python.parent.parent) + ". "
                "Use `& .\\.venv\\Scripts\\python.exe ...` (or `& .\\.venv\\Scripts\\pip.exe ...`) "
                "so you hit the venv, not the system Python."
            )

    # 4 + 5) Anything that would delete or rebuild a `.venv` — through a
    # worktree's junction, or aimed at a primary checkout's real one. Checked
    # last: these are the only rules that read the filesystem, so the three
    # pure-text rules above answer first and these run only on a command that
    # is otherwise fine.
    #
    # Every clause is recorded before anything is refused, because `_lib.block`
    # does not return: an unrecorded block is exactly the blind spot #828 was
    # filed about, one rung further up.
    clauses = destructive_clauses(cmd, _lib.cwd(payload))
    hazard = next((c for c in clauses if c.rule), None)
    for clause in clauses:
        record_destructive_action(
            payload, cmd, clause, "BLOCK" if clause is hazard else "ALLOW")

    if hazard is not None and hazard.rule == "junction":
        try:
            real = os.path.realpath(hazard.venv)
        except OSError:
            real = "(unresolvable)"
        _lib.block(
            "Blocked: `" + hazard.verb + " " + hazard.operand + "` would run through "
            "the .venv junction at " + str(hazard.venv) + " -> " + str(real) + ". "
            "That junction is the primary checkout's REAL venv, shared by every "
            "worktree in this repo and by any app running from it — a recursive "
            "delete or a venv rebuild follows it and guts the primary "
            "(fleet-config#847: this destroyed two repos' venvs in one night, "
            "one of them under a live app). "
            "Use the reparse-safe teardown, which strips the junction first: "
            "`worktree_claim.py remove-worktree <worktree-path>`. "
            "To remove just the junction and keep its target, `rmdir <path>` "
            "with no `/s`."
        )

    # Rule 5. Only reachable with `hazard.rule == "primary"`: `_lib.block` is
    # NoReturn, so the junction branch above never falls through to here.
    if hazard is not None:
        remedy = ("To clean ignored files and keep the venv, add `-e .venv` "
                  "(with `-x`; under `-X` it does not protect it). "
                  if hazard.verb == GIT_CLEAN_VERB else "")
        _lib.block(
            "Blocked: `" + hazard.verb + " " + hazard.operand + "` would delete the "
            "primary checkout's real venv at " + str(hazard.venv) + ". "
            "A `.venv` is machine-local and gitignored, so it is NOT recoverable "
            "from git — the only repair is recreating it and reinstalling every "
            "requirement by hand (fleet-config#828: an unattended lane emptied "
            "one, and it cost a manual rebuild of 33 packages). Every other "
            "session in this repo, and any app running out of this checkout, is "
            "importing from it right now. "
            + remedy +
            "To rebuild a corrupt venv in place, use `python -m venv --clear "
            "<path>` — it replaces the contents without removing the directory "
            "other processes hold open. To tear down a worktree, use "
            "`worktree_claim.py remove-worktree <worktree-path>`. If this venv "
            "really must be deleted outright, that is a human's call — say so "
            "and stop."
        )

    _lib.allow()


if __name__ == "__main__":
    main()
