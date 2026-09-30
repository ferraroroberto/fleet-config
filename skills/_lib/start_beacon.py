"""Interpreter-start beacon for the dead-code inventory (fleet-config#1114). stdlib only.

Step 2 of fleet-config#961: static analysis can't judge a script that only a
human or a desktop shortcut starts, so this records which scripts actually
run. One file, `fleet_start_beacon.pth`, goes into the target repo's `.venv`
site-packages. Python's `site` runs a `.pth` line that starts with `import` at
every interpreter start that loads site (`python.exe` and `pythonw.exe` from
`.venv\\Scripts`). The line appends one tab-separated record to a
machine-local ledger:

    <UTC timestamp>\\t<repo>\\t<abspath of sys.argv[0] | "-m <module>" | "-c" | "">

Guarantees:
- Never raises. Everything runs inside `try`. On failure it writes the error
  once to `%TEMP%\\fleet_start_beacon.<repo>.error` (created only when absent)
  and never to stdout/stderr, so the host script's output is untouched.
- One record per process. On Windows 3.14 `site` processes a venv's
  site-packages twice, so the line would fire twice without a guard (the
  #1114 probe), and a `sys` attribute marks the process as already counted.
  The attribute is per process, so child interpreters still record.
- No network, and nothing but the script path is recorded.
- Self-contained. The `.pth` embeds the code, so it never imports anything from
  this repo and doesn't depend on a fleet-config checkout.

Blind spots, which the inventory reads as missing evidence: `python -S`
(`site` skipped), a script run by an interpreter outside this `.venv`, and
concurrent appends that interleave on Windows (counted as `malformed`).

Ledger: `<hooks state>/dead_code/<repo>/starts.tsv` (gitignored, never
committed). `install` writes a `# installed <utc>` header on a first install
and `uninstall` writes `# uninstalled <utc>`. `entry_inventory.py collect`
reads the headers to tell how much of the window the beacon covers.

    python start_beacon.py install   <repo> [--venv DIR]   # add the .pth (idempotent)
    python start_beacon.py status    <repo> [--venv DIR]
    python start_beacon.py uninstall <repo> [--venv DIR]   # delete the .pth

Removal is deleting the one `.pth` file (`uninstall` also writes the ledger
header). The `.venv` itself is never created, deleted or rebuilt: `install`
refuses when it is missing.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hooks_state import atomic_tmp_prefix, state_dir  # noqa: E402

PTH_NAME = "fleet_start_beacon.pth"
LEDGER_NAME = "starts.tsv"
INSTALLED, UNINSTALLED = "# installed ", "# uninstalled "

# Runs inside `site` at interpreter start, in its own namespace (LEDGER, REPO
# injected). Only modules already loaded by then (sys, os, time) are imported:
# json would pull in `re` and cost milliseconds on every start.
_BEACON = '''
import sys
if not getattr(sys, "_fleet_start_beacon", False):
    sys._fleet_start_beacon = True
    try:
        import os, time
        a = sys.argv[0] if sys.argv else ""
        if a == "-m":
            o = list(getattr(sys, "orig_argv", None) or ())
            m = ""
            for i, x in enumerate(o[1:], 1):
                if x == "-m":
                    m = o[i + 1] if i + 1 < len(o) else ""
                    break
                if x.startswith("-m"):
                    m = x[2:]
                    break
            a = "-m " + m
        elif a and a != "-c":
            a = os.path.abspath(a)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\\t" + REPO + "\\t"
                    + a.replace("\\t", " ").replace("\\r", " ").replace("\\n", " ") + "\\n")
    except Exception as e:
        try:
            import os, time
            p = os.path.join(os.environ.get("TEMP") or os.environ.get("TMP") or ".", "fleet_start_beacon." + REPO + ".error")
            with open(p, "x", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + LEDGER + " " + repr(e)[:300] + "\\n")
        except Exception:
            pass
'''


def ledger_path(repo: Path) -> Path:
    return state_dir() / "dead_code" / repo.resolve().name / LEDGER_NAME


def error_path(repo_name: str) -> Path:
    return Path(tempfile.gettempdir()) / f"fleet_start_beacon.{repo_name}.error"


def site_packages(venv: Path) -> Optional[Path]:
    """The venv's site-packages (Windows `Lib/site-packages`, POSIX `lib/python*/site-packages`)."""
    for cand in [venv / "Lib" / "site-packages", *sorted(venv.glob("lib/python*/site-packages"))]:
        if cand.is_dir():
            return cand
    return None


def pth_path(venv: Path) -> Optional[Path]:
    sp = site_packages(venv)
    return sp / PTH_NAME if sp else None


def render_pth(ledger: Path, repo_name: str) -> str:
    """The one `.pth` line: `import sys; exec(<beacon>, {LEDGER, REPO})`."""
    return f"import sys; exec({_BEACON!r}, {{'LEDGER': {str(ledger)!r}, 'REPO': {repo_name!r}}})\n"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _append_header(ledger: Path, header: str, detail: str) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(f"{header}{_utc_now()} {detail}\n")


def read_ledger(ledger: Path) -> Tuple[List[Tuple[str, str]], Dict[str, str], int]:
    """(headers as (kind, utc), last-hit UTC per raw target, malformed-line count)."""
    headers: List[Tuple[str, str]] = []
    hits: Dict[str, str] = {}
    malformed = 0
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(INSTALLED) or line.startswith(UNINSTALLED):
            kind = "installed" if line.startswith(INSTALLED) else "uninstalled"
            headers.append((kind, line.split(" ")[2]))
            continue
        parts = line.split("\t")
        if len(parts) != 3 or len(parts[0]) != 20 or not parts[0].endswith("Z"):
            malformed += 1 if line.strip() else 0
            continue
        if parts[0] > hits.get(parts[2], ""):
            hits[parts[2]] = parts[0]
    return headers, hits, malformed


def install(repo: Path, venv: Path) -> Dict[str, Any]:
    target = pth_path(venv)
    if target is None:
        raise SystemExit(f"❌ no site-packages under {venv} -- install never creates a venv")
    ledger = ledger_path(repo)
    text = render_pth(ledger, repo.resolve().name)
    first = not target.exists()
    wrote = first or target.read_text(encoding="utf-8") != text
    if wrote:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=atomic_tmp_prefix(target), suffix=".tmp", dir=target.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:  # a starting interpreter never reads a half-written .pth
            f.write(text)
        os.replace(tmp, target)
    if first:
        _append_header(ledger, INSTALLED, f"venv={venv}")
    return {"pth": str(target), "ledger": str(ledger), "first_install": first, "written": wrote}


def uninstall(repo: Path, venv: Path) -> Dict[str, Any]:
    target = pth_path(venv)
    removed = bool(target and target.exists())
    if removed:
        target.unlink()
        _append_header(ledger_path(repo), UNINSTALLED, f"venv={venv}")
    return {"pth": str(target), "removed": removed}


def status(repo: Path, venv: Path) -> Dict[str, Any]:
    target = pth_path(venv)
    ledger = ledger_path(repo)
    present = bool(target and target.exists())
    out: Dict[str, Any] = {"pth": str(target), "installed": present,
                           "current": present and target.read_text(encoding="utf-8") == render_pth(ledger, repo.resolve().name),
                           "ledger": str(ledger), "error_log": None}
    if ledger.exists():
        headers, hits, malformed = read_ledger(ledger)
        out.update(headers=headers, targets=len(hits), last_hit=max(hits.values(), default=None), malformed=malformed)
    err = error_path(repo.resolve().name)
    if err.exists():
        out["error_log"] = err.read_text(encoding="utf-8", errors="replace").strip()
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="interpreter-start beacon for the dead-code inventory (fleet-config#1114)")
    ap.add_argument("command", choices=("install", "uninstall", "status"))
    ap.add_argument("repo")
    ap.add_argument("--venv", default=None, help="target venv (default: <repo>/.venv)")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    venv = Path(args.venv).resolve() if args.venv else repo / ".venv"
    result = {"install": install, "uninstall": uninstall, "status": status}[args.command](repo, venv)
    for key, value in result.items():
        print(f"{key.upper()}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
