"""Build the owner's private walkthrough HTML and store it keyed by `<repo>#<PR>`."""

from __future__ import annotations

import base64
import html
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fleet_core import hooks_state
from ui_proof import proof

STORE_ENV = "UI_WALKTHROUGH_DIR"
# send_file.py accepts files under %TEMP%/claude, so the sendable copy goes there.
SEND_SUBDIR = ("claude", "ui-proof")
# The launcher's outbox caps one file at 15 MB.
SEND_LIMIT_BYTES = 15 * 1024 * 1024

_CSS = """
:root{--bg:#f7f7f8;--card:#fff;--fg:#1d1d1f;--muted:#6e6e73;--line:#d2d2d7;--ok:#1a7f37;--bad:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#111113;--card:#1c1c1e;--fg:#f5f5f7;--muted:#a1a1a6;
--line:#3a3a3c;--ok:#3fb950;--bad:#ff6b6b}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:22px;margin:8px 0}h2{font-size:17px;margin:0 0 4px}
.meta,.small{color:var(--muted);font-size:13px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:10px}
figure{margin:0}figure img{width:100%;height:auto;border:1px solid var(--line);border-radius:8px}
figcaption{font-size:13px;color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}
code{font-size:13px;word-break:break-all}
"""


def store_dir() -> Path:
    """Machine-local walkthrough store, outside every checkout."""
    configured = os.environ.get(STORE_ENV, "").strip()
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "fleet-ui-proof" / "walkthroughs"


def _image(path: str) -> Optional[str]:
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _leg_figure(entry: dict) -> str:
    leg = f"{entry.get('viewport')} · {entry.get('theme')}"
    errors = sum(proof.finding_fails(f) for f in entry.get("findings") or [])
    verdict = '<span class="ok">lint clean</span>' if not errors else f'<span class="bad">{errors} error(s)</span>'
    src = _image(str(entry.get("screenshot", "")))
    img = f'<img alt="{html.escape(leg)}" src="{src}">' if src else '<p class="bad">screenshot missing</p>'
    return f"<figure>{img}<figcaption>{html.escape(leg)} — {verdict}</figcaption></figure>"


def _state_section(state: str, criterion: Optional[str], entries: List[dict]) -> str:
    legs = [e for e in entries if e.get("state") == state]
    heading = html.escape(criterion) if criterion else "Other captured state"
    return (f"<section><h2>{heading}</h2><div class=\"small\">state <code>{html.escape(state)}</code></div>"
            f"<div class=\"grid\">{''.join(_leg_figure(e) for e in legs)}</div></section>")


def render(repo_name: str, pr: int, sha: str, title: str, summary: str,
           criteria: Dict[str, str], entries: List[dict]) -> str:
    """The self-contained page: every criterion's state first, then any other captured state."""
    errors = proof.failing_lines(entries)
    lint = ('<p class="ok">Layout lint: clean on every captured leg.</p>' if not errors else
            '<p class="bad">Layout lint errors:</p><ul>'
            + "".join(f"<li><code>{html.escape(line)}</code></li>" for line in errors) + "</ul>")
    others = sorted({str(e.get("state")) for e in entries} - set(criteria))
    sections = [_state_section(s, c, entries) for s, c in criteria.items()]
    sections += [_state_section(s, None, entries) for s in others]
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    head = html.escape(title or f"{repo_name}#{pr}")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Walkthrough {html.escape(repo_name)}#{pr}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{head}</h1><div class=\"meta\">{html.escape(repo_name)} PR #{pr} · merge "
        f"<code>{html.escape(sha)}</code> · {when} · {len(criteria)} criteria, {len(entries)} legs</div>"
        + (f"<p>{html.escape(summary)}</p>" if summary else "")
        + lint + "".join(sections) + "</main></body></html>"
    )


def _update_index(folder: Path, key: str, row: dict) -> None:
    index_path = folder / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = {}
    if not isinstance(index, dict):
        index = {}
    index[key] = row
    hooks_state.atomic_write_text(index_path, json.dumps(index, indent=2))


def build(repo: Path, repo_name: str, pr: int, sha: str, title: str = "", summary: str = "") -> List[str]:
    """Write the walkthrough, index it, stage a sendable copy; return KEY=value lines."""
    rec = proof.load_record(repo)
    if rec is None:
        return ["WALKTHROUGH=not_built reason=no proof recorded for this branch (`ui_proof record` first)"]
    entries = proof.load_manifest(Path(str(rec.get("manifest", ""))))
    if entries is None:
        return [f"WALKTHROUGH=not_built reason=recorded manifest {rec.get('manifest')} is unreadable"]
    page = render(repo_name, pr, sha, title, summary, rec.get("criteria") or {}, entries)
    folder = store_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / f"{repo_name}-{pr}.html"
    hooks_state.atomic_write_text(stored, page)
    key = f"{repo_name}#{pr}"
    _update_index(folder, key, {"path": str(stored), "sha": sha, "title": title,
                                "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    send_dir = Path(tempfile.gettempdir()).joinpath(*SEND_SUBDIR)
    send_dir.mkdir(parents=True, exist_ok=True)
    send_copy = send_dir / stored.name
    shutil.copyfile(stored, send_copy)
    size = stored.stat().st_size
    lines = [f"WALKTHROUGH={stored}", f"KEY={key}", f"SEND_PATH={send_copy}", f"BYTES={size}"]
    if size > SEND_LIMIT_BYTES:
        lines.append(f"SEND_WARNING=over the {SEND_LIMIT_BYTES // (1024 * 1024)} MB outbox cap; "
                     "capture fewer legs or smaller states")
    return lines
