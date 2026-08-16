"""Concurrency primitive for the issue-* skills: claim-or-worktree.

Two top-level agent sessions working the **same** repo used to collide because
they shared one working directory — a `git checkout` in session B rewrote the
tree under session A mid-build (fleet-config#143). The collision window is not
branch-cut time; it is the minutes-long *study* phase before an agent writes
anything. By the time the second agent cuts its branch, the damage is done.

The fix is first-come-first-served, claimed at the **very first action**:

  - The first session to `acquire` a repo wins its **primary checkout** and works
    on `main` in place, exactly as before.
  - Every session after that gets `MODE=worktree` and builds in an isolated
    sibling worktree (`<repo>-wt-<N>`) on its own branch, sharing the repo's
    object store. Separate HEAD + separate files ⇒ no checkout/HEAD race.
  - `release` (on finish, from the primary) frees the claim for the next session.

The claim is a directory published **atomically, fully populated** (meta.json
included) via a temp-dir-then-`rename` under the repo's *common* git dir
(`git rev-parse --git-common-dir`), which every worktree of the repo shares —
so the claim is visible from the primary checkout and from every linked
worktree (see `_publish_claim`; a bare `mkdir`-then-write-`meta.json` let two
racers both win, fleet-config#334). Exactly one racer wins the rename; the
rest fall through to worktree mode. A crashed session's claim is reclaimed
once it ages past the TTL (no fragile PID-liveness check on Windows).

Windows-specific by design (the fleet is Windows): worktree `.venv` is a
**junction** to the primary's `.venv` (`mklink /J`) — worktrees don't share
untracked files, and a 24-repo fleet can't recreate heavy venvs per worktree.
The teardown order is load-bearing: a junction MUST be stripped with
`rmdir` (reparse-safe, no `/s`) BEFORE `git worktree remove`, or git's recursive
delete follows the junction and wipes the *real* venv (proven the hard way; same
junction footgun as uninstall.ps1, fleet-config#136).

`.venv` isn't always the only gitignored path a repo's own gate needs — a
vendored install (`local-llm-hub`'s `vendor/comfyui/`), a model cache, and
similar never make it into a fresh worktree either, so that repo's own test
suite can never pass there even though the build itself succeeds
(fleet-config#620). A repo declares extra ones in its own `.fleet.toml`:

    [worktree]
    extra_junctions = ["vendor/comfyui"]

`worktree_junction_targets` reads this declaratively; `.venv` is always
junctioned first and remains the *only* target when no `.fleet.toml` /
`[worktree]` table is present, so an undeclared repo behaves exactly as
before. Every target — `.venv` and each declared extra — goes through the
same junction-then-reparse-safe-teardown path in `setup_worktree` /
`remove_worktree`; a declared path that doesn't exist in the primary is
simply skipped, never a setup failure.

`git worktree add` populates tracked files only, so a repo's own gitignored
runtime config (`config/webapp_config.json`, `config/apps.json`, ... whatever
each repo's own `config.json`-pattern requires) never makes it into the new
worktree. Left unfixed, an e2e suite that boots a disposable webapp+session-host
hits its own missing-config guard for nearly every test and mass-skips silently
-- the pre-ship gate still reports green (fleet-config#470). `setup_worktree`
copies the primary's `config/*.json` (excluding `*.sample.json` templates) into
the worktree right after the checkout is created.

Subcommands:

  acquire <repo-root> [--issue N] [--branch B] [--ttl-hours H] [--force-worktree]
      Atomically claim the primary checkout. Prints `MODE=primary` (work in
      place) or `MODE=worktree` (caller then calls setup-worktree). Reclaims a
      claim older than the TTL, or one whose recorded branch no longer exists
      (merged-and-deleted => leaked claim, self-heals on the next acquire).

      Worktree mode is **forced**, with no primary attempt, whenever either
      `--force-worktree` is passed OR `APP_LAUNCHER_SESSION_ID` is set (an
      App-Launcher-dispatched session: a machine chose the work, not a human).
      Set `WORKTREE_CLAIM_ALLOW_PRIMARY=1` to defeat the environment trigger
      for a dispatched flow that genuinely must hold the primary; an explicit
      `--force-worktree` still wins over it. Enforcing this in the tool rather
      than in skill prose is deliberate -- fleet-config#525 first shipped as a
      SKILL.md instruction and a dispatched worker ignored it inside the hour.

      `--force-worktree` skips the claim attempt entirely and always prints
      `MODE=worktree` (fleet-config#515). The claim only protects against a
      second *claiming session*; a live production process sitting in the same
      directory — the launcher webapp, home-automation's tray, or fleet-config's
      own hooks/ + skills/ junctioned into every live `~/.claude` — is not a
      claim holder, so a first-and-only unattended agent legitimately wins
      `MODE=primary` and edits files that a running app is serving. Unattended
      fleet-fanout dispatch (cleanup-fleet, cleanup-fleet-all, codebase-audit's
      security self-heal) therefore never works a primary checkout, for any
      repo. Interactive single-session `/issue-start` keeps the default
      claim-or-worktree behaviour.

  setup-worktree <repo-root> <issue-N> <branch>
      `git worktree add <repo>-wt-<N> -b <branch> <origin-main>` + junction
      `.venv` and any repo-declared `[worktree] extra_junctions` (fleet-config#620)
      into it, then copy the primary's gitignored `config/*.json` runtime
      config (excluding `*.sample.json` templates, which are already tracked)
      into the worktree. Prints `WORKTREE=<path>`.

  release <repo-root>
      Remove the primary claim. Idempotent. (Worktree sessions never hold it.)

  assert-owner <repo-root> <issue-N>
      Guard before a primary-tree `git checkout <main>` (issue-finish step 5,
      issue-start step 4): refuses (exit 1) if the tree is dirty or the claim
      is held by a *different* issue; passes (exit 0) if the tree is clean and
      the claim is free or already owned by <issue-N>. Fleet-config#473.

  land-primary <repo-root> <issue-N>
      Make a worktree lane's merge *live*: fast-forward the primary checkout,
      or report why it couldn't. Same guard as assert-owner (clean tree, claim
      free or owned by <issue-N>) plus "already on the default branch", then
      `pull --ff-only` and a `rev-list --count HEAD..origin/<default>` check.
      Prints one `PRIMARY=live behind=0` / `PRIMARY=stale reason=<why>` line in
      every outcome -- the line the finish summary quotes. Exit 0 only when
      live. Never checks out, stashes or forces the primary: a refusal is
      reported, not recovered. Fleet-config#647.

  remove-worktree <worktree-path> [--force-nonstandard-name]
      Reparse-safe teardown: strip the .venv junction, then
      `git worktree remove --force` + `git worktree prune`. Tolerates a
      worktree that git already deregistered while its directory survived
      (fleet-config#526) -- it resolves the primary by inverting the
      `<repo>-wt-<N>` convention and falls back to a plain delete, always
      after the junction strip. Exits 1, naming the path, if the tree is
      still there afterwards (a live process is holding a file inside it);
      the caller must report that as residue, never as a clean teardown.
      The usual holder is a leaked e2e browser helper -- `remove_worktree`'s
      docstring names the sweep to run before retrying.

      Refuses (exit 2, deletes nothing) unless the target actually **is** a
      linked worktree: a primary checkout (`--git-common-dir` == `--git-dir`)
      is rejected outright, and a path whose basename doesn't match the
      `<repo>-wt-<N>` convention is rejected unless `--force-nonstandard-name`
      is passed. An agent that confuses this subcommand's `worktree_path` arg
      with every sibling subcommand's `repo_root` arg previously got the
      primary checkout `rmtree`'d -- fleet-config#589, which destroyed a
      repo's gitignored personal data that existed nowhere else.

  status <repo-root>
      Print the current claim holder (if any) and `git worktree list`.

stdlib + the `git` CLI only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402
from no_window import NO_WINDOW  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

ensure_utf8_stdio()

LOCK_NAME = "issue-claim.lock"
META_NAME = "meta.json"
DEFAULT_TTL_HOURS = 8.0


# ---- pure helpers (unit-tested without git) -------------------------------

WT_SEP = "-wt-"

# Band a worktree's copied runtime-config ports are repointed into (#537).
# Rationale for these exact bounds is in `worktree_port`'s docstring.
WT_PORT_BASE = 8500
WT_PORT_SPAN = 500


def worktree_path(repo_root: Path, issue: str) -> Path:
    """Sibling worktree path: `<parent>/<repo-name>-wt-<N>`.

    Same convention as /issue-batch. The `<repo>-wt-<N>` *prefix-matches* the
    repo's `cwd_prefix` in projects.toml, so notify_on_idle still names the right
    project (a `.worktrees/` layout would break that match). `primary_for_worktree`
    inverts this, so `WT_SEP` is the one place the separator is spelled.
    """
    return repo_root.parent / f"{repo_root.name}{WT_SEP}{issue}"


def is_stale(
    meta: Optional[dict],
    now: float,
    ttl_hours: float,
    branch_exists: Optional["Callable[[str], bool]"] = None,
) -> bool:
    """A claim is stale once it ages past the TTL, its meta is unreadable, or its
    recorded branch no longer exists.

    No PID-liveness check: a one-shot helper invocation can't capture the
    long-lived agent-session PID, and Windows PID checks are unreliable. The TTL
    is the crash-safety valve — a generous default so a legitimately long build
    is never reclaimed out from under itself.

    `branch_exists` is an injected predicate (`branch -> bool`) so this stays a
    pure, git-free function for the unit tests; the CLI wires in a git-backed
    one. When a claim records a `branch` and that branch is already
    merged-and-deleted, the claim is definitionally done — treat it as stale so a
    leaked claim self-heals on the next `acquire` instead of blocking for the
    full TTL (fleet-config#174). A claim with no recorded branch, or when no
    predicate is supplied, falls back to the TTL alone.
    """
    if not meta:
        return True
    created = meta.get("created")
    try:
        if (now - float(created)) > ttl_hours * 3600.0:
            return True
    except (TypeError, ValueError):
        return True
    branch = meta.get("branch")
    if branch and branch_exists is not None and not branch_exists(branch):
        return True
    return False


def read_meta(lock_dir: Path) -> Optional[dict]:
    try:
        return json.loads((lock_dir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(lock_dir: Path, meta: dict) -> None:
    (lock_dir / META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _publish_claim(lock_dir: Path, meta: dict) -> bool:
    """Atomically create `lock_dir` **fully populated** with meta.json.

    `mkdir` then a separate `write_meta` (the pre-#334 shape) leaves a window
    where `lock_dir` exists but meta.json doesn't yet — a concurrent racer that
    hits `FileExistsError` on its own `mkdir` in that window reads `None` meta,
    treats it as stale, and reclaims (`rmtree` + re-`mkdir`) out from under the
    first winner, which then blindly overwrites meta and *also* returns
    "primary" (fleet-config#334: two sessions both got MODE=primary on the same
    repo, silently losing one session's uncommitted work).

    Fix: write meta into a private temp dir first (invisible to every other
    process — its name is never guessed), then publish with one `Path.rename`.
    On Windows `os.rename` never replaces an existing destination — it raises
    if `lock_dir` already exists — so the rename is the single atomic
    compare-and-swap: `lock_dir` is only ever observed either fully absent or
    fully populated, never in between.
    """
    tmp_dir = lock_dir.with_name(f"{lock_dir.name}.tmp-{os.getpid()}-{time.time_ns()}")
    tmp_dir.mkdir(parents=False)
    write_meta(tmp_dir, meta)
    try:
        tmp_dir.rename(lock_dir)
        return True
    except OSError:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False


def owner_check(holder: Optional[dict], issue: str, dirty: bool) -> Tuple[bool, str]:
    """Decide whether `issue` may switch this checkout's primary tree to main.

    Pure decision for the `assert-owner` guard (fleet-config#473): a checkout
    that lands a merge (`/issue-finish` step 5) or syncs main (`/issue-start`
    step 4) must refuse rather than blindly `git checkout <main>` when either
    (a) the tree is dirty — uncommitted work that isn't this session's to
    switch out from under, or (b) the claim is live and held by a *different*
    issue. Passes when the tree is clean and the claim is free or already
    owned by `issue`. Returns `(ok, reason)`; `reason` is the human-readable
    line the CLI prints either way.
    """
    if dirty:
        return False, "working tree has uncommitted changes"
    if holder and str(holder.get("issue")) != str(issue):
        return False, (f"primary held since {holder.get('created_iso', '?')} "
                        f"by issue {holder.get('issue', '?')} on {holder.get('branch', '?')}")
    return True, ("free" if not holder else "owned")


def land_primary_check(
    holder: Optional[dict],
    issue: str,
    dirty: bool,
    current_branch: str,
    main_branch: str,
) -> Tuple[bool, str]:
    """Decide whether it is safe to fast-forward the primary checkout.

    Pure decision for the `land-primary` guard (fleet-config#647). A worktree
    lane's merge is authoritative on the remote but *not live* until the
    primary tree is fast-forwarded -- acutely so in this repo, where `hooks/`
    and `skills/` reach `~/.claude` through junctions rooted at the primary, so
    a merged hook or skill change does nothing fleet-wide until that tree moves.

    The landing is a `pull --ff-only` and nothing else. `owner_check` supplies
    the "is this tree anyone else's to touch?" half; the branch check enforces
    the other half -- only ever pull a tree *already sitting on* its default
    branch, because a worktree lane must never `git checkout` the primary.
    Returns `(ok, reason)`; on refusal `reason` is the text that reaches the
    finish summary as `PRIMARY=stale reason=<reason>`.
    """
    ok, reason = owner_check(holder, issue, dirty)
    if not ok:
        return False, reason
    if not current_branch:
        return False, f"primary is on a detached HEAD, not '{main_branch}'"
    if current_branch != main_branch:
        return False, f"primary is on '{current_branch}', not '{main_branch}'"
    return True, f"clean, on {main_branch}, claim {reason}"


def format_primary_state(ok: bool, reason: str, behind: Optional[int] = None) -> str:
    """The one machine-readable line a worktree lane's finish summary carries.

    `PRIMARY=live behind=0`, or `PRIMARY=stale reason=<why>`. "Merged" and
    "live" are two separate facts: a lane that could not establish the second
    reports it as its own named state rather than letting silence imply
    success. An uncountable `behind` is likewise stale, never passing -- the
    fleet's unknown-is-not-pass rule.
    """
    if not ok:
        return f"PRIMARY=stale reason={reason}"
    if behind == 0:
        return "PRIMARY=live behind=0"
    if behind is None or behind < 0:
        return "PRIMARY=stale reason=could not count commits behind origin"
    return f"PRIMARY=stale reason=still {behind} behind after pull --ff-only"


def try_acquire(
    lock_dir: Path,
    meta: dict,
    now: float,
    ttl_hours: float,
    branch_exists: Optional["Callable[[str], bool]"] = None,
) -> Tuple[str, dict]:
    """Atomically claim `lock_dir`. Returns ('primary', meta) on win, else
    ('worktree', holder-meta). Reclaims a stale lock; loses a reclaim race
    gracefully to worktree mode. Pure filesystem — hermetic, no git; the
    optional `branch_exists` predicate (passed straight to `is_stale`) is the
    only seam where the CLI injects git, so the tests stay hermetic.
    """
    if _publish_claim(lock_dir, meta):
        return "primary", meta
    holder = read_meta(lock_dir)
    if not is_stale(holder, now, ttl_hours, branch_exists):
        return "worktree", holder or {}
    # Stale -> reclaim. rmtree + re-publish isn't atomic as a pair, so a
    # concurrent reclaimer may win the re-publish; that racer's failed rename
    # below sends it to worktree mode. Net: exactly one ends up primary.
    shutil.rmtree(lock_dir, ignore_errors=True)
    if _publish_claim(lock_dir, meta):
        return "primary", meta
    return "worktree", read_meta(lock_dir) or {}


# ---- git / junction ops (Windows; thin subprocess wrappers) ---------------

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return git_run.run_git(["-C", str(repo), *args], check=check)


def common_dir(repo: Path) -> Path:
    """The shared git dir (one per repo, visible from every worktree)."""
    out = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return Path(out)


def lock_dir_for(repo: Path) -> Path:
    return common_dir(repo) / LOCK_NAME


def main_ref(repo: Path) -> str:
    """origin's default branch, e.g. 'origin/main'. Falls back to origin/main.

    Routed through the shared `git_run.resolve_default_branch_ref` resolver
    (fleet-config#500); `candidates=()` + `final_fallback="origin/main"`
    reproduce this helper's own pre-existing symbolic-ref-only contract.
    """
    return git_run.resolve_default_branch_ref(repo, candidates=(), final_fallback="origin/main")


def branch_exists_on_remote(repo: Path, branch: str) -> bool:
    """True if `branch` still exists on origin (or as a local ref).

    Used to detect a leaked claim whose branch is already merged-and-deleted
    (fleet-config#174). Shells to `git ls-remote` — stdlib + git CLI only, per
    the module contract; no new dependency. On any git failure (offline, no
    remote, transient error) we return True: an inability to *prove* the branch
    is gone must never reclaim a possibly-live claim out from under a peer.
    """
    if not branch:
        return True
    res = _git(repo, "ls-remote", "--heads", "origin", branch, check=False)
    if res.returncode != 0:
        return True  # can't tell -> assume present, fall back to the TTL
    if res.stdout.strip():
        return True
    # Not on the remote — it may be a local-only branch not yet pushed.
    local = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    return local.returncode == 0


def is_primary_checkout(repo: Path) -> bool:
    """True for the primary checkout, False for a linked worktree."""
    git_dir = _git(repo, "rev-parse", "--path-format=absolute", "--git-dir").stdout.strip()
    return Path(git_dir).resolve() == common_dir(repo).resolve()


def _common_dir_or_none(path: Path) -> Optional[Path]:
    """`common_dir(path)`, or None when `path` isn't inside a git checkout.

    The shared git dir is the repo's identity: it is the *same* absolute path
    seen from the primary and from every linked worktree, which is what lets
    `mode_check` decide "same repo?" without deciding "same tree?".
    """
    try:
        return common_dir(path).resolve()
    except (subprocess.CalledProcessError, OSError):
        return None


def mode_check(cwd_common: Optional[Path], arg_common: Optional[Path]) -> Tuple[bool, str]:
    """Can `mode` answer about the cwd checkout? Returns `(ok, reason)`.

    Pure reconciliation half of `cmd_mode` (fleet-config#652). Both arguments
    are shared-git-dir paths, so the comparison is at **repo** granularity, not
    tree granularity: a bare repo name resolved to the primary while the caller
    stands in `<repo>-wt-<N>` is a *match*, because the argument only says which
    repo the cwd is expected to belong to. Naming a genuinely different repo —
    or standing outside git entirely — is the caller error worth surfacing.
    """
    if cwd_common is None:
        return False, "cwd is not inside a git checkout"
    if arg_common is None:
        return False, "repo argument is not inside a git checkout"
    if cwd_common.resolve() != arg_common.resolve():
        return False, (f"repo argument names a different repo than the cwd "
                       f"({arg_common} vs {cwd_common})")
    return True, "repo argument and cwd agree on the repo"


def _is_primary_checkout_safe(path: Path) -> bool:
    """`is_primary_checkout`, tolerant of `path` not being a git checkout at all.

    `remove_worktree`'s guard (fleet-config#589) must never itself explode on
    an unexpected target -- a path that isn't a git repo can't be a primary
    checkout either, so this returns False rather than raising, leaving the
    naming-convention guard to catch that case instead.
    """
    try:
        return is_primary_checkout(path)
    except subprocess.CalledProcessError:
        return False


def _looks_like_worktree_name(name: str) -> bool:
    """True if `name` matches the `<repo>{WT_SEP}<N>` naming convention.

    Belt-and-braces companion to the primary-checkout guard (fleet-config#589):
    a `remove-worktree` target whose basename doesn't look like a worktree at
    all is refused too, even if it somehow isn't detected as a primary
    checkout (e.g. not a git repo). Mirrors `primary_for_worktree`'s own
    `rpartition(WT_SEP)` inversion -- `WT_SEP` stays the one spelling of the
    separator.
    """
    stem, sep, issue = name.rpartition(WT_SEP)
    return bool(sep) and bool(stem) and bool(issue)


def worktree_junction_targets(repo: Path) -> list:
    """Relative paths to junction from the primary checkout into a fresh worktree.

    Always starts with `.venv`. A repo can declare extra gitignored paths its
    own gate needs (a vendored install, a model cache, ...) via `.fleet.toml`'s
    optional `[worktree]` table:

        [worktree]
        extra_junctions = ["vendor/comfyui"]

    No `.fleet.toml`, no `[worktree]` table, or a malformed value all degrade
    to the `.venv`-only default -- an undeclared repo behaves exactly as
    before (fleet-config#620). Entries that aren't non-empty strings, or that
    contain a `..` component, are silently dropped rather than junctioning
    outside the repo. Pure path/text logic -- no filesystem checks here;
    `setup_worktree` skips a declared target that doesn't actually exist in
    the primary.
    """
    targets = [".venv"]
    fleet_toml = repo / ".fleet.toml"
    if not fleet_toml.is_file():
        return targets
    import tomllib
    try:
        data = tomllib.loads(fleet_toml.read_text(encoding="utf-8", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError):
        # OSError covers a partially-deleted/leftover worktree whose .fleet.toml
        # exists but is unreadable (permission error, vanished mid-read, ...) --
        # remove_worktree must still strip .venv rather than crash before it
        # gets the chance to (fleet-config#620 teardown-safety follow-up).
        return targets
    table = data.get("worktree")
    if not isinstance(table, dict):
        return targets
    extra = table.get("extra_junctions")
    if not isinstance(extra, list):
        return targets
    for item in extra:
        if not isinstance(item, str) or not item.strip():
            continue
        rel = item.strip().strip("/\\")
        if rel and ".." not in Path(rel).parts:
            targets.append(rel)
    return targets


def _junction(link: Path, target: Path) -> Tuple[bool, str]:
    """Create a directory junction `link` -> `target` (`mklink /J`).

    Creates `link`'s parent first -- `git worktree add` only populates tracked
    files, so a declared extra target's parent dir (e.g. `vendor/` for
    `vendor/comfyui`) may not exist yet. Returns `(ok, message)`; message is
    the combined stderr/stdout on failure, empty on success. Pure OS wrapper --
    the caller decides whether a failure is fatal.
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True, creationflags=NO_WINDOW,
    )
    ok = res.returncode == 0 and link.exists()
    return ok, "" if ok else (res.stderr.strip() or res.stdout.strip())


def _strip_junction(path: Path) -> None:
    """Remove a directory junction by its reparse point ONLY, never its target.

    `rmdir` without `/s` removes an empty dir or a reparse point and refuses a
    non-empty real dir — so it can NEVER recurse into a junction's target. This
    is the load-bearing step: doing it before `git worktree remove` is what
    keeps the primary's real .venv intact (fleet-config#136 / #143).
    """
    if path.exists() or path.is_symlink():
        subprocess.run(
            ["cmd", "/c", "rmdir", str(path)],
            capture_output=True, text=True, creationflags=NO_WINDOW,
        )


def _port_is_free(port: int) -> bool:
    """True if nothing is listening on 127.0.0.1:`port` right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def worktree_port(issue: str, taken: "set" = frozenset()) -> int:
    """A port for a worktree's copied runtime config, in `8500-8999`.

    Deterministic first, honest second: the issue number seeds the offset so
    re-running setup for the same lane reproduces the same port, then we probe
    upward (wrapping inside the band) past anything already listening or already
    handed out to a sibling config file in this same worktree. A repo with two
    ported configs (app-launcher's webapp + session-host) therefore gets two
    distinct ports rather than one collided pair.

    The band is chosen to clear three things at once: the fleet's own app ports
    (`844x`), this machine's known fixed listeners (cloudflared 20241-3,
    tailscaled 40746, OneDrive 42050, MouseWithoutBorders 15100/1, llama-server
    18093, StreamDeck 28196/8, MSI 26822/32683/33683, logioptionsplus 19010,
    hwinfo 10000), and the Windows ephemeral range 49152-65535.
    """
    digits = "".join(ch for ch in issue if ch.isdigit())
    seed = int(digits) if digits else sum(ord(ch) for ch in issue)
    for step in range(WT_PORT_SPAN):
        port = WT_PORT_BASE + ((seed + step) % WT_PORT_SPAN)
        if port not in taken and _port_is_free(port):
            return port
    raise RuntimeError(
        f"no free port in {WT_PORT_BASE}-{WT_PORT_BASE + WT_PORT_SPAN - 1} "
        f"for worktree issue {issue!r}"
    )


def _repoint_config_port(dst: Path, taken: "set") -> Optional[int]:
    """Give a copied config its own port instead of the primary's. Returns it.

    Only a top-level integer `port` on a JSON **object** is touched — nested
    objects are left alone, and a file that is not an object, has no `port`, or
    does not parse is returned unchanged with `None`. A broken runtime config
    must not break worktree setup; it is the app's business to complain about
    its own file, not ours to fail the lane over.
    """
    try:
        raw = json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port_value = raw.get("port") if isinstance(raw, dict) else None
    # `bool` is an `int` subclass — exclude it explicitly, a `true` is not a port.
    if not isinstance(port_value, int) or isinstance(port_value, bool):
        return None
    port = worktree_port(dst.parent.parent.name.rpartition(WT_SEP)[2], taken)
    raw["port"] = port
    dst.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return port


def copy_runtime_config(repo: Path, wt: Path) -> list:
    """Copy the primary's gitignored `config/*.json` into a fresh worktree.

    `git worktree add` populates tracked files only, so a repo's own
    gitignored runtime config (`config/webapp_config.json`, `config/apps.json`,
    ...) never lands in the new worktree. An e2e suite that boots a disposable
    webapp+session-host then hits its own missing-config guard for nearly
    every test and mass-skips silently, while the pre-ship gate still reports
    green (fleet-config#470). `*.sample.json` templates are already tracked
    and excluded; a destination file that already exists (e.g. a prior partial
    setup) is left alone rather than overwritten. No-op if the repo has no
    `config/` dir. Returns the list of copied destination paths.

    The copy is byte-verbatim **except for a top-level `port`**, which is
    repointed into the `8500-8999` band (fleet-config#537). Carrying the
    primary's port across is what made every worktree lane's e2e suite report a
    collision with the user's live tray and refuse to run — a false positive,
    since the suite boots its own disposable instance on a free port and never
    touches the tray's. It also left a worktree that actually boots the app
    trying to bind the primary's port. Secrets (`auth_token`, `auth_password`)
    and every other field still copy across untouched: the worktree must stay a
    faithful runtime twin, differing only where sharing is the bug.
    """
    copied = []
    src_dir = repo / "config"
    if not src_dir.is_dir():
        return copied
    dst_dir = wt / "config"
    assigned: set = set()
    for src in sorted(src_dir.glob("*.json")):
        if src.name.endswith(".sample.json"):
            continue
        dst = dst_dir / src.name
        if dst.exists():
            continue
        dst_dir.mkdir(exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
        port = _repoint_config_port(dst, assigned)
        if port is not None:
            assigned.add(port)
            print(f"WORKTREE_PORT={port} ({src.name})", file=sys.stderr)
    return copied


def setup_worktree(repo: Path, issue: str, branch: str) -> Path:
    wt = worktree_path(repo, issue)
    if wt.exists():
        sys.exit(f"Worktree path already exists: {wt}\n"
                 f"Probably stale — clean with: git -C {repo} worktree remove --force {wt}")
    _git(repo, "worktree", "add", str(wt), "-b", branch, main_ref(repo))
    copied = copy_runtime_config(repo, wt)
    if copied:
        print(f"CONFIG_COPIED={len(copied)}: "
              f"{', '.join(p.name for p in copied)}", file=sys.stderr)

    linked: list = []
    for rel in worktree_junction_targets(repo):
        src = repo / rel
        if not src.is_dir():
            # Declared-but-absent must not break setup (fleet-config#620
            # acceptance) -- .venv itself is optional the same way today.
            continue
        link = wt / rel
        ok, err = _junction(link, src)
        if not ok:
            # Roll back the half-made worktree so we never leave a broken one
            # behind. Strip every junction made so far FIRST (reparse-safe,
            # including this failed/partial one) so the rollback remove can't
            # follow any of them into the primary's real target.
            for done in linked:
                _strip_junction(done)
            _strip_junction(link)
            _git(repo, "worktree", "remove", "--force", str(wt), check=False)
            sys.exit(f"Failed to junction {rel} into the worktree: {err}")
        linked.append(link)
    return wt


def primary_for_worktree(wt: Path) -> Optional[Path]:
    """The primary checkout owning `wt`, or None if it can't be determined.

    Normally `git rev-parse --git-common-dir` from inside the worktree. That
    fails once the worktree has been **deregistered** but its directory still
    exists (its `.git` file is gone, so git exits 128) -- precisely the leftover
    a teardown is called to clean, and the state that used to crash
    `remove_worktree` with an unhandled CalledProcessError (fleet-config#526).
    So fall back to inverting `worktree_path`'s own `<repo>-wt-<N>` convention;
    that stays a single source of truth for the naming rather than a second
    hand-rolled guess. Returns None when neither route resolves, leaving the
    caller to degrade honestly rather than pretend.
    """
    try:
        return common_dir(wt).parent  # common dir is <primary>/.git
    except (subprocess.CalledProcessError, OSError):
        pass
    stem, sep, _issue = wt.name.rpartition(WT_SEP)
    if sep and stem:
        candidate = wt.parent / stem
        if (candidate / ".git").exists():
            return candidate
    return None


def remove_worktree(wt: Path, *, force_nonstandard_name: bool = False) -> int:
    """Reparse-safe teardown. Returns 0 on success, 1 if the tree survived.

    Refuses (returns 2, deletes nothing) before touching anything if `wt`
    isn't actually a linked worktree (fleet-config#589): a primary checkout
    (`--git-common-dir` == `--git-dir`) is always rejected, and a basename
    that doesn't match the `<repo>{WT_SEP}<N>` convention is rejected too
    unless `force_nonstandard_name` is set. This is what stops a caller that
    confuses this function's `wt` arg with every sibling subcommand's
    `repo_root` arg from `rmtree`-ing a repo's main working tree.

    Never raises on a leftover it was asked to clean: a directory that outlives
    its registration (a live process holding a file open inside it is the usual
    cause -- a leaked browser helper or backend server) must produce an honest
    non-zero exit naming the path, so the caller reports residue instead of a
    false clean.

    **When the removal fails as "busy", suspect a leaked browser helper.** The
    named cause (project-scaffolding#203) is a Playwright/WebKit helper process
    left behind by an e2e run, holding the worktree as its **current working
    directory** -- helpers inherit pytest's cwd, so a run inside `<repo>-wt-<N>`
    leaves helpers rooted there, and Windows will not delete a directory that is
    some live process's cwd. An *already-exited* helper cannot be the culprit:
    Windows merely keeps its process object (and its `tasklist`/WMI row) alive
    while a handle to it remains, so a busy worktree implies a genuinely
    **running** holder, not one of those zombies.

    Diagnosing and clearing that is the e2e harness's job, not this module's.
    Run the adopting repo's own standalone sweep through its venv interpreter,
    then retry the teardown:

        <repo>/.venv/Scripts/python.exe tests/e2e/_browser_sweep.py <worktree-path> [--dry-run]

    Pointer only -- the sweep classifies by cwd read from the PEB (Win32
    exposes no accessor for another process's working directory) and lives in
    `project-scaffolding`'s e2e harness. Never reimplement it here.
    """
    if not wt.exists():
        print(f"Worktree already gone: {wt}")
        return 0

    if _is_primary_checkout_safe(wt):
        print(f"REFUSING: {wt} is a primary checkout, not a linked worktree -- "
              f"deleting it would destroy the repo's main working tree "
              f"(fleet-config#589). Nothing was touched.", file=sys.stderr)
        return 2

    if not force_nonstandard_name and not _looks_like_worktree_name(wt.name):
        print(f"REFUSING: {wt.name!r} doesn't match the '<repo>{WT_SEP}<N>' worktree "
              f"naming convention -- refusing to delete a path that doesn't look like "
              f"a worktree. Pass --force-nonstandard-name to override "
              f"(fleet-config#589). Nothing was touched.", file=sys.stderr)
        return 2

    # MUST precede any recursive delete, on EVERY path through this function
    # (see _strip_junction): git's remove follows a junction into its real
    # target and destroys it. `.fleet.toml` is a tracked file, so it was
    # already checked out into `wt` by `git worktree add` -- reading targets
    # from `wt` itself (not the primary) needs no git and degrades safely if
    # the worktree is already deregistered (fleet-config#526/#620).
    for rel in worktree_junction_targets(wt):
        _strip_junction(wt / rel)

    primary = primary_for_worktree(wt)
    if primary is not None:
        _git(primary, "worktree", "remove", "--force", str(wt), check=False)
        _git(primary, "worktree", "prune", check=False)
    else:
        print(f"# could not resolve the primary for {wt}; "
              f"stripped the junction and falling back to a plain delete",
              file=sys.stderr)

    if wt.exists():
        # git declined (or was never reachable) and the tree is still here.
        # The junction is already gone, so this delete cannot escape the tree.
        shutil.rmtree(wt, ignore_errors=True)

    if wt.exists():
        print(f"Worktree NOT removed (a live process is likely holding a file "
              f"inside it): {wt}", file=sys.stderr)
        print(f"# most likely a leaked Playwright/WebKit helper from an e2e run, "
              f"holding the worktree as its cwd (project-scaffolding#203). "
              f"Clear it with that repo's own sweep, then retry:\n"
              f"#   <repo>/.venv/Scripts/python.exe tests/e2e/_browser_sweep.py "
              f"{wt} --dry-run", file=sys.stderr)
        return 1
    print(f"Removed worktree: {wt}")
    return 0


# ---- CLI ------------------------------------------------------------------

def _resolve_path_arg(arg: str) -> Optional[Path]:
    """Resolve a path-ish CLI arg, tolerating the bare-name forms agents pass.

    Returns the resolved existing path, or None if nothing plausible exists.
    Three shapes are accepted: an ordinary relative/absolute path; the current
    repo's own name from inside it (-> CWD, fleet-config#162); and a sibling
    worktree name from the parent repo (-> CWD.parent/<name>, fleet-config#165 —
    e.g. `remove-worktree fleet-config-wt-7` run from inside `fleet-config`).
    """
    repo = Path(arg).resolve()
    if repo.exists():
        return repo
    # Only a bare, non-absolute name (no directory components) gets the fallback.
    p = Path(arg)
    if not p.is_absolute() and p.parent == Path("."):
        cwd = Path.cwd()
        if cwd.name.casefold() == arg.casefold():
            return cwd
        sibling = cwd.parent / arg
        if sibling.exists():
            return sibling
    return None


def _resolve_repo(arg: str) -> Path:
    resolved = _resolve_path_arg(arg)
    if resolved is None:
        sys.exit(f"No such repo path: {Path(arg).resolve()}")
    return resolved


def worktree_forced(argv_flag: bool, env: "Mapping[str, str]") -> Tuple[bool, str]:
    """Decide whether this `acquire` may return `MODE=primary` at all.

    Returns `(forced, why)`. Two independent triggers, either sufficient:

      - the caller passed `--force-worktree` (unattended fleet fanout asking
        for it explicitly), or
      - `APP_LAUNCHER_SESSION_ID` is set, meaning App Launcher's Board spawned
        this session: a machine dispatched the work and no human chose the tree.

    The second trigger exists because the first one *did not hold* in practice.
    fleet-config#525 originally shipped as a line of `/issue-start` SKILL.md
    prose telling the agent to pass the flag, and within the hour a dispatched
    worker landed in a primary checkout anyway with everything wired correctly —
    the variable set, the junctioned skill updated, the flow chained. A rule an
    agent has to read, recognise and choose to obey is advisory; every other
    guarantee in this lifecycle (the claim FSM, the junction-strip ordering, the
    halt gate) is enforced by code that cannot be skipped, and so is this now.

    `WORKTREE_CLAIM_ALLOW_PRIMARY=1` is the deliberate escape hatch, for the
    rare dispatched flow that genuinely must hold the primary. It only defeats
    the environment trigger — an explicit `--force-worktree` still wins, since
    a caller that asked for isolation by name should get it.
    """
    if argv_flag:
        return True, "--force-worktree"
    if env.get("APP_LAUNCHER_SESSION_ID") and env.get("WORKTREE_CLAIM_ALLOW_PRIMARY") != "1":
        return True, "APP_LAUNCHER_SESSION_ID (launcher-dispatched; fleet-config#525)"
    return False, ""


def cmd_acquire(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo_root)
    forced, why = worktree_forced(getattr(args, "force_worktree", False), os.environ)
    if forced:
        args.force_worktree = True
        if why != "--force-worktree":
            print(f"# forced to worktree mode by {why}", file=sys.stderr)
    if getattr(args, "force_worktree", False):
        # No claim attempt at all: unattended fanout must never touch a primary
        # checkout, even an unclaimed one (fleet-config#515 — a live app or a
        # live junction is not a claim holder). Deliberately does not publish a
        # claim either, so the primary stays free for a human session.
        print("MODE=worktree")
        print("# --force-worktree: primary checkout not eligible for unattended "
              "fanout; caller must run setup-worktree", file=sys.stderr)
        return 0
    lock = lock_dir_for(repo)
    meta = {
        "created": time.time(),
        "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "issue": args.issue,
        "branch": args.branch,
        "repo": str(repo),
    }
    mode, holder = try_acquire(
        lock, meta, time.time(), args.ttl_hours,
        branch_exists=lambda b: branch_exists_on_remote(repo, b),
    )
    print(f"MODE={mode}")
    if mode == "worktree" and holder:
        print(f"# primary held since {holder.get('created_iso', '?')} "
              f"by issue {holder.get('issue', '?')} on {holder.get('branch', '?')}",
              file=sys.stderr)
    return 0


def cmd_setup_worktree(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo_root)
    wt = setup_worktree(repo, args.issue, args.branch)
    print(f"WORKTREE={wt}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo_root)
    lock = lock_dir_for(repo)
    if lock.exists():
        shutil.rmtree(lock, ignore_errors=True)
        print(f"Released claim on {repo}")
    else:
        print(f"No claim to release on {repo}")
    return 0


def cmd_remove_worktree(args: argparse.Namespace) -> int:
    # Tolerate the bare sibling-name form (fleet-config#165). When nothing
    # resolves, hand the literal resolved path to remove_worktree so it prints
    # its honest "already gone" message rather than exiting.
    resolved = _resolve_path_arg(args.worktree_path)
    return remove_worktree(
        resolved or Path(args.worktree_path).resolve(),
        force_nonstandard_name=args.force_nonstandard_name,
    )


def cmd_assert_owner(args: argparse.Namespace) -> int:
    """Refuse a `git checkout <main>` unless it's safe for `<issue>` to run it.

    Called immediately before the checkout in `/issue-finish` step 5 and
    `/issue-start` step 4 (fleet-config#473) — the claim system routes a
    *second* session into a worktree, but says nothing about whether the
    process about to switch the primary tree's HEAD is actually the claim
    holder. Exits 0 (`ASSERT_OWNER=pass`) when the tree is clean and the claim
    is free or owned by this issue; exits 1 (`ASSERT_OWNER=refuse: <reason>`)
    otherwise, printing the same "primary held since ..." shape the claim
    system already uses so a human or chief sees why the checkout stopped.
    """
    repo = _resolve_repo(args.repo_root)
    lock = lock_dir_for(repo)
    holder = read_meta(lock) if lock.exists() else None
    dirty = bool(_git(repo, "status", "--porcelain").stdout.strip())
    ok, reason = owner_check(holder, args.issue, dirty)
    if ok:
        print(f"ASSERT_OWNER=pass ({reason})")
        return 0
    print(f"ASSERT_OWNER=refuse: {reason}", file=sys.stderr)
    return 1


def cmd_land_primary(args: argparse.Namespace) -> int:
    """Fast-forward the primary checkout after a worktree lane's merge, or say
    plainly that it didn't (fleet-config#647).

    Prints exactly one `PRIMARY=` line on stdout in every outcome -- that line
    is what the finish summary quotes. Exit 0 only for `PRIMARY=live behind=0`;
    1 for any stale state; 2 when the target isn't a primary checkout at all.
    Never checks out, stashes, forces, or otherwise "recovers" a tree it was
    refused: reporting stale *is* the correct outcome, and `--ff-only` fails
    safely rather than merging, so nothing here can lose work.
    """
    repo = _resolve_repo(args.repo_root)
    if not _is_primary_checkout_safe(repo):
        print(f"PRIMARY=stale reason=not a primary checkout: {repo}")
        return 2
    lock = lock_dir_for(repo)
    holder = read_meta(lock) if lock.exists() else None
    dirty = bool(_git(repo, "status", "--porcelain").stdout.strip())
    current = _git(repo, "branch", "--show-current").stdout.strip()
    ref = main_ref(repo)                                   # e.g. 'origin/main'
    main_branch = ref.split("/", 1)[1] if "/" in ref else ref
    ok, reason = land_primary_check(holder, args.issue, dirty, current, main_branch)
    if not ok:
        print(format_primary_state(False, reason))
        return 1
    pull = _git(repo, "pull", "--ff-only", check=False)
    if pull.returncode != 0:
        detail = ((pull.stderr or "") + (pull.stdout or "")).strip().splitlines()
        print(format_primary_state(False, f"pull --ff-only failed: {detail[0] if detail else '?'}"))
        return 1
    counted = _git(repo, "rev-list", "--count", f"HEAD..{ref}", check=False).stdout.strip()
    behind = int(counted) if counted.isdigit() else -1
    line = format_primary_state(True, reason, behind)
    print(line)
    return 0 if behind == 0 else 1


def cmd_mode(args: argparse.Namespace) -> int:
    """Print `primary` or `worktree` for the checkout the caller is *standing in*
    — the deterministic decision /issue-finish keys its teardown on.

    The answer is about the **cwd**, never about `repo_root` (fleet-config#652).
    `repo_root` only *identifies* which repo the cwd is expected to belong to;
    it does not select a tree. That distinction is the entire bug: every skill
    documents the call as `mode <repo>` with a bare name, and from inside
    `<repo>-wt-<N>` the sibling fallback in `_resolve_path_arg` (fleet-config#165,
    right for `remove-worktree`) resolves that name to the *primary*. The old
    implementation then asked `is_primary_checkout` about the primary and
    truthfully answered `primary` — to a worktree lane. Silent, and always wrong
    in the dangerous direction: the primary teardown path runs `gh pr merge
    --delete-branch` and hits `'main' is already used by worktree`.

    An argument that cannot be reconciled with the cwd prints `UNKNOWN
    reason=<why>` and exits 2 — a helper that cannot establish which checkout it
    was asked about says so rather than guessing.
    """
    resolved = _resolve_path_arg(args.repo_root)
    if resolved is None:
        print(f"UNKNOWN reason=no such repo path: {Path(args.repo_root).resolve()}")
        return 2
    cwd = Path.cwd()
    ok, reason = mode_check(_common_dir_or_none(cwd), _common_dir_or_none(resolved))
    if not ok:
        print(f"UNKNOWN reason={reason}")
        return 2
    print("primary" if is_primary_checkout(cwd) else "worktree")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    repo = _resolve_repo(args.repo_root)
    lock = lock_dir_for(repo)
    holder = read_meta(lock) if lock.exists() else None
    if holder:
        print(f"CLAIM=held  issue={holder.get('issue')}  branch={holder.get('branch')}  "
              f"since={holder.get('created_iso')}")
    else:
        print("CLAIM=free")
    print(_git(repo, "worktree", "list", check=False).stdout.strip())
    return 0


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(prog="worktree_claim", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("acquire", help="atomically claim the primary checkout")
    a.add_argument("repo_root")
    a.add_argument("--issue", default=None)
    a.add_argument("--branch", default=None)
    a.add_argument("--ttl-hours", type=float, default=DEFAULT_TTL_HOURS)
    a.add_argument("--force-worktree", action="store_true",
                   help="never claim the primary; always print MODE=worktree "
                        "(unattended fleet fanout, fleet-config#515)")
    a.set_defaults(func=cmd_acquire)

    s = sub.add_parser("setup-worktree", help="create the sibling worktree + junction .venv")
    s.add_argument("repo_root")
    s.add_argument("issue")
    s.add_argument("branch")
    s.set_defaults(func=cmd_setup_worktree)

    r = sub.add_parser("release", help="release the primary claim (idempotent)")
    r.add_argument("repo_root")
    r.set_defaults(func=cmd_release)

    rw = sub.add_parser("remove-worktree", help="reparse-safe worktree teardown")
    rw.add_argument("worktree_path")
    rw.add_argument("--force-nonstandard-name", action="store_true",
                     help="skip the <repo>-wt-<N> naming-convention guard "
                          "(fleet-config#589)")
    rw.set_defaults(func=cmd_remove_worktree)

    ao = sub.add_parser("assert-owner", help="refuse a main checkout unless it's safe for <issue>")
    ao.add_argument("repo_root")
    ao.add_argument("issue")
    ao.set_defaults(func=cmd_assert_owner)

    lp = sub.add_parser("land-primary",
                        help="fast-forward the primary after a worktree merge, or report stale")
    lp.add_argument("repo_root")
    lp.add_argument("issue")
    lp.set_defaults(func=cmd_land_primary)

    md = sub.add_parser("mode",
                        help="print 'primary' or 'worktree' for the cwd checkout "
                             "(run it from the checkout you are asking about)")
    md.add_argument("repo_root",
                    help="the repo the cwd must belong to — it identifies the repo, "
                         "it does NOT select a tree; a mismatch prints "
                         "'UNKNOWN reason=...' and exits 2 (fleet-config#652)")
    md.set_defaults(func=cmd_mode)

    st = sub.add_parser("status", help="show claim holder + worktree list")
    st.add_argument("repo_root")
    st.set_defaults(func=cmd_status)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
