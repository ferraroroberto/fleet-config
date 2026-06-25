"""Tailnet-cert drift detection for the web-app conformance sweep (fleet-config#210).

Single source of truth for "is this a Tailscale-reachable app that still ships
*only* the self-signed-CA + `/install-ca` mobileconfig trust dance, instead of
the `tailscale cert` (real Let's Encrypt) standard?". `/design-sync` calls this
right after its pre-flight — reusing the web-app population it already enumerates
— and files a deduped `cert-drift` issue when this reports drift. Same
deterministic-not-LLM principle as `ux_surface.py`: the verdict is a fixed truth
table over three file-presence signals, not a per-run judgment call.

Canonical decision record for the standard: `ferraroroberto/project-scaffolding#89`.
Reference impl (migrated, must read CLEAN): `ferraroroberto/grocery-shopping-automation`
(`scripts/gen_tailscale_cert.py` + webapp wire-up).

The three signals (gathered from the repo's tracked files):
  - **tailnet_reachable** — `README.md` / `CLAUDE.md` / `docs/**/*.md` mention a
    `*.ts.net` URL or Tailscale access (docs/ is in scope because a real target
    documents its tailnet exposure there, not in the README). A genuinely LAN-only
    app has no such signal, so its self-signed path is legitimate and must NOT trip
    the check.
  - **self_signed_present** — a `gen_ssl_cert.py`-style provisioner, or an
    `/install-ca` route / `.mobileconfig` trust dance.
  - **ts_cert_present** — a `gen_tailscale_cert.py`-style provisioner, or a
    `tailscale cert` invocation. Its presence means the app already migrated.

Subcommand:

  detect <repo-root>
      Gather the signals and print the verdict. Output contract:
        CERT_DRIFT=yes|no
        REASON=<one line>
        TAILNET=<file:line | ->
        SELF_SIGNED=<file:line | ->
        TS_CERT=<file:line | ->
      Always exits 0 (a non-web / LAN-only repo simply reports CERT_DRIFT=no).

stdlib + the `git` CLI only (matches the _lib module contract).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):  # UTF-8 even when stdout is captured (cp1252 fallback)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


# ---- pure signal predicates (unit-tested without git) ---------------------

# A *.ts.net URL or a plain "tailscale" mention (case-insensitive). Scoped to
# README/CLAUDE at the IO layer so an incidental mention elsewhere can't trip it.
TAILNET_RE = re.compile(r"\.ts\.net\b|\btailscale\b", re.IGNORECASE)

# The self-signed trust dance: the `/install-ca` route, an `install_ca` handler,
# or a `.mobileconfig` profile served to phones.
INSTALL_CA_RE = re.compile(r"install[-_]ca\b|\bmobileconfig\b", re.IGNORECASE)

# A real `tailscale cert ...` invocation in a script — as a shell string
# (`tailscale cert host`) or an argv list (`['tailscale', 'cert', ...]`). The
# separator class allows the quotes/commas of an argv list but not word chars,
# so "tailscale for cert renewal" (prose) does not match.
TS_CERT_CMD_RE = re.compile(r"\btailscale\b['\"\s,]+cert\b", re.IGNORECASE)

# Filename of a self-signed cert generator (gen_ssl_cert.py & kin). Requires
# "ssl" so it can never match the tailscale provisioner below.
SELF_SIGNED_SCRIPT_RE = re.compile(
    r"(?:gen|make|create|build)[_-][a-z_]*ssl[a-z_]*cert[a-z_]*\.py$", re.IGNORECASE
)

# Filename of a tailscale-cert provisioner (gen_tailscale_cert.py & kin).
TS_CERT_SCRIPT_RE = re.compile(
    r"(?:gen|make|create|build)[_-][a-z_]*tailscale[a-z_]*cert[a-z_]*\.py$", re.IGNORECASE
)


def has_tailnet_signal(text: str) -> bool:
    """True if the text mentions a *.ts.net URL or Tailscale."""
    return bool(TAILNET_RE.search(text))


def has_install_ca(text: str) -> bool:
    """True if the text references the `/install-ca` / mobileconfig trust dance."""
    return bool(INSTALL_CA_RE.search(text))


def has_tailscale_cert_cmd(text: str) -> bool:
    """True if the text invokes `tailscale cert`."""
    return bool(TS_CERT_CMD_RE.search(text))


def is_self_signed_cert_script(basename: str) -> bool:
    """True for a `gen_ssl_cert.py`-style self-signed provisioner filename."""
    return bool(SELF_SIGNED_SCRIPT_RE.search(basename))


def is_tailscale_cert_script(basename: str) -> bool:
    """True for a `gen_tailscale_cert.py`-style provisioner filename."""
    return bool(TS_CERT_SCRIPT_RE.search(basename))


def classify(tailnet_reachable: bool, self_signed_present: bool, ts_cert_present: bool) -> tuple[str, str]:
    """The fixed truth table: ('drift'|'clean', reason).

    Order matters — each early return encodes one no-false-positive guarantee:
      1. no self-signed provisioning at all -> nothing to migrate;
      2. no Tailscale signal -> LAN-only, self-signed is the correct choice;
      3. a tailscale-cert provisioner exists -> already migrated (clean even if a
         legacy self-signed script still lingers).
    Only a tailnet-reachable app that is *still* self-signed-only, with no
    `tailscale cert` provisioner, is drift.
    """
    if not self_signed_present:
        return ("clean", "no self-signed HTTPS provisioning — nothing to migrate")
    if not tailnet_reachable:
        return ("clean", "no Tailscale signal — self-signed CA is legitimate for a LAN-only app")
    if ts_cert_present:
        return ("clean", "already provisions HTTPS via `tailscale cert` — migrated")
    return (
        "drift",
        "tailnet-reachable and still provisions HTTPS only via a self-signed CA; "
        "no `tailscale cert` provisioner present",
    )


# ---- IO layer: gather the signals from a repo -----------------------------

_TEXT_EXTS = {
    ".py", ".bat", ".ps1", ".sh", ".md", ".toml", ".cfg",
    ".ini", ".txt", ".html", ".js", ".json", ".yml", ".yaml",
}
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}


def _list_files(repo_root: Path) -> List[str]:
    """Tracked files as posix relpaths. Prefer `git ls-files` (respects
    .gitignore — never scans `.venv`); fall back to a pruned walk for a
    non-git tree (keeps the helper testable over a plain temp dir)."""
    res = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if res.returncode == 0 and res.stdout.strip():
        return [ln.strip().replace("\\", "/") for ln in res.stdout.splitlines() if ln.strip()]

    out: List[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), repo_root).replace("\\", "/")
            out.append(rel)
    return out


def _read(repo_root: Path, rel: str) -> str:
    try:
        return (repo_root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def gather_signals(repo_root: Path) -> Dict[str, Dict[str, object]]:
    """Walk the repo's tracked files and resolve the three signals, each with
    a `file:line` (or `file`) evidence pointer for the issue body."""
    files = _list_files(repo_root)
    tailnet: Dict[str, object] = {"present": False, "evidence": ""}
    self_signed: Dict[str, object] = {"present": False, "evidence": ""}
    ts_cert: Dict[str, object] = {"present": False, "evidence": ""}

    # 1. filename signals
    for rel in files:
        base = rel.rsplit("/", 1)[-1]
        if not self_signed["present"] and is_self_signed_cert_script(base):
            self_signed = {"present": True, "evidence": rel}
        if not ts_cert["present"] and is_tailscale_cert_script(base):
            ts_cert = {"present": True, "evidence": rel}

    # 2. content signals (tailnet scoped to README/CLAUDE + docs/*.md; the rest,
    #    any source). docs/ is included because a real target (voice-transcriber)
    #    documents its tailnet exposure in docs/webapp-architecture.md, not the
    #    README — scoping to README/CLAUDE alone false-negatives it. The signal is
    #    still AND-gated with a self-signed provisioner + no tailscale-cert, so a
    #    passing doc mention can't flag a LAN-only app on its own.
    for rel in files:
        if tailnet["present"] and self_signed["present"] and ts_cert["present"]:
            break
        low = rel.lower()
        base = rel.rsplit("/", 1)[-1].lower()
        ext = os.path.splitext(base)[1]
        if ext not in _TEXT_EXTS:
            continue
        is_tailnet_doc = base in ("readme.md", "claude.md") or (
            low.startswith("docs/") and ext == ".md"
        )
        for i, line in enumerate(_read(repo_root, rel).splitlines(), 1):
            if is_tailnet_doc and not tailnet["present"] and has_tailnet_signal(line):
                tailnet = {"present": True, "evidence": f"{rel}:{i}"}
            if not self_signed["present"] and has_install_ca(line):
                self_signed = {"present": True, "evidence": f"{rel}:{i}"}
            if not ts_cert["present"] and has_tailscale_cert_cmd(line):
                ts_cert = {"present": True, "evidence": f"{rel}:{i}"}

    return {"tailnet": tailnet, "self_signed": self_signed, "ts_cert": ts_cert}


def cmd_detect(repo_root: Path) -> int:
    sig = gather_signals(repo_root)
    verdict, reason = classify(
        bool(sig["tailnet"]["present"]),
        bool(sig["self_signed"]["present"]),
        bool(sig["ts_cert"]["present"]),
    )
    print(f"CERT_DRIFT={'yes' if verdict == 'drift' else 'no'}")
    print(f"REASON={reason}")
    print(f"TAILNET={sig['tailnet']['evidence'] or '-'}")
    print(f"SELF_SIGNED={sig['self_signed']['evidence'] or '-'}")
    print(f"TS_CERT={sig['ts_cert']['evidence'] or '-'}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Tailnet-cert drift detector for /design-sync.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_detect = sub.add_parser("detect", help="is this a tailnet app still on the self-signed dance?")
    p_detect.add_argument("repo", type=Path)

    args = ap.parse_args(argv)
    repo = args.repo.resolve()
    if not repo.is_dir():
        print(f"Not a directory: {repo}", file=sys.stderr)
        return 2
    return cmd_detect(repo)


if __name__ == "__main__":
    raise SystemExit(main())
