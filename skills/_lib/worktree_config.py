"""Worktree runtime-config provisioning: ports + machine-bound value blanking.

Split out of `worktree_claim.py` (fleet-config#731) -- port allocation and
config blanking share no state with the claim FSM, junction teardown, or the
primary-landing guard that module owns, and are exercised by their own tests.
Same shape `design_lint` was split into at a lower line-count threshold
(fleet-config#564), and the `steer_delivery.py` extraction out of
`chief_ops.py` (fleet-config#680).

`git worktree add` populates tracked files only, so a repo's own gitignored
runtime config (`config/*.json`, root-level `*.json` such as
`accounting-quarterly`'s `config.json`, and `.env`) never makes it into a
fresh worktree. Left unfixed, an e2e suite that boots a disposable
webapp+session-host hits its own missing-config guard for nearly every test
and mass-skips silently -- the pre-ship gate still reports green
(fleet-config#470, fleet-config#714). `copy_runtime_config` / `copy_root_config`
/ `copy_env_file` close that gap; `worktree_claim.setup_worktree` calls all
three right after the checkout is created.

A byte-verbatim copy is not safe by itself: a repo's config can point at a
real, machine-bound sink outside the worktree -- a synced mirror/backup
folder, an index path, another repo's database -- and a worktree instance
booted for a "ready to validate" handoff then reads and writes the owner's
**real** data (`task-os#80`, fleet-config#713). `blank_machine_bound_config`
blanks those values right after the port rewrite. A repo declares exactly
which dotted keys are machine-bound in its own `.fleet.toml`:

    [worktree]
    blank_config_keys = ["mirror.dir", "mirror.backup_dir", "search.folder_roots"]

`worktree_blank_config_keys` reads this the same way `worktree_claim`'s
`worktree_junction_targets` reads `extra_junctions`. A repo that declares
nothing falls back to a conservative built-in default (`_blank_default_heuristic`):
any string value, anywhere in the config, that looks machine-bound -- an
`{onedrive}`-style placeholder, or an absolute Windows path -- is blanked,
list entries filtered the same way.

Carrying the primary's port across into a copied config is what made every
worktree lane's e2e suite report a collision with the user's live tray and
refuse to run -- a false positive, since the suite boots its own disposable
instance on a free port and never touches the tray's (fleet-config#537).
`worktree_port` deterministically assigns each copied config its own port in
the `8500-8999` band, seeded from the issue number so re-running setup for
the same lane reproduces the same port.

This module is deliberately import-free of `worktree_claim.py` (which imports
*this* module, re-exporting the names its existing callers and tests use) --
a `_git` wrapper and the worktree-suffix separator are duplicated here in
miniature rather than imported back, to keep the dependency a one-way DAG.

stdlib + the `git` CLI only.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import git_run  # noqa: E402

# Mirrors worktree_claim.WT_SEP -- duplicated (not imported) so this module
# stays import-free of worktree_claim.py, per the module docstring above.
_WT_SEP = "-wt-"

# Band a worktree's copied runtime-config ports are repointed into (#537).
# Rationale for these exact bounds is in `worktree_port`'s docstring.
WT_PORT_BASE = 8500
WT_PORT_SPAN = 500


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return git_run.run_git(["-C", str(repo), *args], check=check)


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


def _repoint_config_port(dst: Path, wt: Path, taken: "set") -> Optional[int]:
    """Give a copied config its own port instead of the primary's. Returns it.

    Only a top-level integer `port` on a JSON **object** is touched — nested
    objects are left alone, and a file that is not an object, has no `port`, or
    does not parse is returned unchanged with `None`. A broken runtime config
    must not break worktree setup; it is the app's business to complain about
    its own file, not ours to fail the lane over.

    `wt` is the worktree root itself (not `dst`'s parent) so the issue number
    seeding `worktree_port` is read from `wt.name` directly — correct whether
    `dst` sits under `config/` or right at the worktree root (fleet-config#714).
    """
    try:
        raw = json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    port_value = raw.get("port") if isinstance(raw, dict) else None
    # `bool` is an `int` subclass — exclude it explicitly, a `true` is not a port.
    if not isinstance(port_value, int) or isinstance(port_value, bool):
        return None
    port = worktree_port(wt.name.rpartition(_WT_SEP)[2], taken)
    raw["port"] = port
    dst.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return port


_MACHINE_BOUND_TOKEN = "{onedrive}"
_WIN_ABS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _looks_machine_bound(value: str) -> bool:
    """True if a string value plausibly points at a real, non-worktree path.

    Two shapes cover every case seen so far (fleet-config#713): a template
    placeholder a repo's own config layer resolves against a real synced
    folder (`{onedrive}/...`), and a plain Windows absolute path (a drive
    letter into the primary checkout, another repo's data dir, or a synced
    folder written out in full). Deliberately narrow -- e.g. it does not
    flag URLs or POSIX-shaped strings that are actually route paths -- since
    a false positive here blanks a value a repo's own gate may need; a repo
    that needs finer control declares `blank_config_keys` explicitly instead
    of relying on this default.
    """
    if not value:
        return False
    if _MACHINE_BOUND_TOKEN in value.lower():
        return True
    return bool(_WIN_ABS_PATH_RE.match(value))


def _blank_declared_keys(raw: dict, keys: list) -> list:
    """Blank exactly the dotted keys a repo declared, skipping any that are
    absent -- a declared-but-absent key must not break setup, same contract
    as `worktree_junction_targets`'s declared-but-absent path."""
    blanked = []
    for dotted in keys:
        parts = [p for p in dotted.split(".") if p]
        if not parts:
            continue
        node = raw
        for part in parts[:-1]:
            if not (isinstance(node, dict) and part in node):
                node = None
                break
            node = node[part]
        if not isinstance(node, dict) or parts[-1] not in node:
            continue
        last = parts[-1]
        value = node[last]
        if isinstance(value, str) and value:
            node[last] = ""
            blanked.append(dotted)
        elif isinstance(value, list) and value:
            node[last] = []
            blanked.append(dotted)
    return blanked


def _blank_default_heuristic(node, prefix: str = "") -> list:
    """Blank every machine-bound-looking string leaf, recursively.

    The conservative fallback for a repo that declares no
    `blank_config_keys` -- see `_looks_machine_bound`. List entries are
    filtered rather than blanked in place (a `folder_roots`-shaped list of
    paths just loses the dangerous entries); nested dicts/lists are walked.
    """
    blanked = []
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, str):
                if _looks_machine_bound(value):
                    node[key] = ""
                    blanked.append(path)
            elif isinstance(value, list):
                filtered = [v for v in value if not (isinstance(v, str) and _looks_machine_bound(v))]
                if len(filtered) != len(value):
                    node[key] = filtered
                    blanked.append(path)
                for item in value:
                    if isinstance(item, (dict, list)):
                        blanked.extend(_blank_default_heuristic(item, path))
            elif isinstance(value, dict):
                blanked.extend(_blank_default_heuristic(value, path))
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                blanked.extend(_blank_default_heuristic(item, prefix))
    return blanked


def worktree_blank_config_keys(repo: Path) -> Optional[list]:
    """Dotted config keys a repo declares as machine-bound, from `.fleet.toml`:

        [worktree]
        blank_config_keys = ["mirror.dir", "mirror.backup_dir"]

    Returns `None` when nothing is declared (no `.fleet.toml`, no
    `[worktree]` table, no `blank_config_keys` key, or a malformed value) --
    the caller's signal to fall back to the conservative default heuristic.
    Returns a (possibly empty) list when the repo explicitly declares one; an
    empty list is a deliberate opt-out of the default heuristic entirely.
    Same silent-degrade-on-any-error contract as `worktree_junction_targets`.
    """
    fleet_toml = repo / ".fleet.toml"
    if not fleet_toml.is_file():
        return None
    import tomllib
    try:
        data = tomllib.loads(fleet_toml.read_text(encoding="utf-8", errors="replace"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    table = data.get("worktree")
    if not isinstance(table, dict) or "blank_config_keys" not in table:
        return None
    keys = table.get("blank_config_keys")
    if not isinstance(keys, list):
        return None
    return [k.strip() for k in keys if isinstance(k, str) and k.strip()]


def blank_machine_bound_config(dst: Path, declared_keys: Optional[list]) -> list:
    """Blank machine-bound path values in a just-copied worktree config.

    `declared_keys` is `worktree_blank_config_keys(repo)`'s result -- `None`
    routes through the default heuristic, a list (including empty) blanks
    exactly those dotted keys. Same fail-open contract as
    `_repoint_config_port`: a file that isn't a JSON object, or doesn't
    parse, is left untouched rather than breaking setup. Returns the dotted
    keys actually blanked, for the caller to report.
    """
    try:
        raw = json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    if declared_keys is not None:
        blanked = _blank_declared_keys(raw, declared_keys)
    else:
        blanked = _blank_default_heuristic(raw)
    if blanked:
        dst.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return blanked


def copy_runtime_config(repo: Path, wt: Path, assigned: Optional["set"] = None) -> list:
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
    repointed into the `8500-8999` band (fleet-config#537), and any
    machine-bound path values, which are blanked (fleet-config#713, see
    `blank_machine_bound_config`). Carrying the primary's port across is what
    made every worktree lane's e2e suite report a collision with the user's
    live tray and refuse to run — a false positive, since the suite boots its
    own disposable instance on a free port and never touches the tray's. It
    also left a worktree that actually boots the app trying to bind the
    primary's port. Secrets (`auth_token`, `auth_password`) and every other
    field still copy across untouched: the worktree must stay a faithful
    runtime twin, differing only where sharing (a live port, a real synced
    folder) is itself the bug.

    `assigned` collects ports handed out in this call; pass in a set shared
    with a sibling call (e.g. `copy_root_config` on the same worktree) so two
    ported configs in different locations can't compute the same port
    (fleet-config#714 review).
    """
    copied = []
    src_dir = repo / "config"
    if not src_dir.is_dir():
        return copied
    dst_dir = wt / "config"
    assigned = set() if assigned is None else assigned
    declared_keys = worktree_blank_config_keys(repo)
    for src in sorted(src_dir.glob("*.json")):
        if src.name.endswith(".sample.json"):
            continue
        dst = dst_dir / src.name
        if dst.exists():
            continue
        dst_dir.mkdir(exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
        port = _repoint_config_port(dst, wt, assigned)
        if port is not None:
            assigned.add(port)
            print(f"WORKTREE_PORT={port} ({src.name})", file=sys.stderr)
        blanked = blank_machine_bound_config(dst, declared_keys)
        if blanked:
            print(f"WORKTREE_CONFIG_BLANKED={len(blanked)}: "
                  f"{', '.join(blanked)} ({src.name})", file=sys.stderr)
    return copied


def copy_env_file(repo: Path, wt: Path) -> Optional[Path]:
    """Copy the primary's gitignored `.env` into a fresh worktree.

    `git worktree add` populates tracked files only, so a repo's own
    `.env` never lands in the new worktree, exactly like `config/*.json`
    (see `copy_runtime_config`). A hub or webapp that reads secrets/config
    from `.env` at startup (e.g. `local-llm-hub`'s `LOCAL_LLM_HUB_SSH_KEY`)
    then stalls on its own missing-value path until an unrelated timeout
    fires, and the e2e stage of a pre-ship gate goes red for a reason that
    has nothing to do with the diff (fleet-config#698). No-op, never a setup
    failure, if the primary has no `.env`. A destination file that already
    exists (e.g. a prior partial setup) is left alone rather than
    overwritten, same as `copy_runtime_config`. Returns the copied
    destination path, or None if there was nothing to copy.
    """
    src = repo / ".env"
    if not src.is_file():
        return None
    dst = wt / ".env"
    if dst.exists():
        return None
    shutil.copy2(src, dst)
    return dst


def _git_check_ignore(repo: Path, names: list) -> "set":
    """Names among `names` (repo-root-relative) that git considers ignored.

    `git check-ignore` exits 1 when nothing matches -- not an error, so this
    runs with `check=False` and trusts only stdout, one matched name per line.
    """
    if not names:
        return set()
    res = _git(repo, "check-ignore", "--", *names, check=False)
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def copy_root_config(repo: Path, wt: Path, assigned: Optional["set"] = None) -> list:
    """Copy the primary's gitignored root-level `*.json` into a fresh worktree.

    Some repos keep their runtime config at the repo root instead of under
    `config/` (`accounting-quarterly`'s `config.json`, `home-automation`'s
    `devices.json`) -- `copy_runtime_config` doesn't reach those, so a
    worktree for one of these repos was missing its config entirely
    (fleet-config#714). Which root-level `*.json` files matter is asked of
    git itself (`git check-ignore`) rather than hardcoded, so a repo that
    adds a new one is covered with no code change here; a tracked
    `*.sample.json` template is excluded up front as belt-and-braces, though
    a tracked file is never gitignored in the first place. Same rules as
    `copy_runtime_config`: an existing destination is left alone, a
    top-level `port` is repointed via `_repoint_config_port`, and
    machine-bound values are blanked via `blank_machine_bound_config`.
    Returns the list of copied destination paths.

    `assigned` collects ports handed out in this call; `setup_worktree`
    passes the same set it gave `copy_runtime_config` so a `config/*.json`
    file and a root-level file can't be repointed to the same port.
    """
    copied = []
    candidates = sorted(p.name for p in repo.glob("*.json") if not p.name.endswith(".sample.json"))
    ignored = _git_check_ignore(repo, candidates)
    if not ignored:
        return copied
    assigned = set() if assigned is None else assigned
    declared_keys = worktree_blank_config_keys(repo)
    for name in sorted(ignored):
        src = repo / name
        dst = wt / name
        if dst.exists():
            continue
        shutil.copy2(src, dst)
        copied.append(dst)
        port = _repoint_config_port(dst, wt, assigned)
        if port is not None:
            assigned.add(port)
            print(f"WORKTREE_PORT={port} ({name})", file=sys.stderr)
        blanked = blank_machine_bound_config(dst, declared_keys)
        if blanked:
            print(f"WORKTREE_CONFIG_BLANKED={len(blanked)}: "
                  f"{', '.join(blanked)} ({name})", file=sys.stderr)
    return copied
