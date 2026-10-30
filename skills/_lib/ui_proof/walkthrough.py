"""Build `.proof/<PR>/index.html`: per criterion, what changed, then before and after side by side."""

from __future__ import annotations

import base64
import html
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from ui_proof import proof, store

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
h1{font-size:22px;margin:8px 0}h2{font-size:17px;margin:0 0 4px}h3{font-size:14px;margin:12px 0 4px}
.meta,.small{color:var(--muted);font-size:13px}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:14px 0}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:10px}
figure{margin:0}figure img{width:100%;height:auto;border:1px solid var(--line);border-radius:8px}
figcaption{font-size:13px;color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}
code{font-size:13px;word-break:break-all}
"""


def _image(path: Path) -> Optional[str]:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _figure(folder: Path, row: Optional[dict], phase: str) -> str:
    src = _image(folder / str(row.get("file"))) if row else None
    img = f'<img alt="{phase}" src="{src}">' if src else f'<p class="small">no {phase} capture</p>'
    note = ""
    if row and "findings" in row:
        errors = sum(proof.finding_fails(f) for f in row["findings"])
        note = ' — <span class="ok">lint clean</span>' if not errors else f' — <span class="bad">{errors} error(s)</span>'
    return f"<figure>{img}<figcaption>{phase}{note}</figcaption></figure>"


def _legs(rows: List[dict], state: str) -> Dict[str, dict]:
    return {f"{r.get('viewport')}-{r.get('theme')}": r for r in rows if r.get("state") == state}


def _state_section(folder: Path, data: dict, state: str, lines: List[str]) -> str:
    before, after = _legs(data.get("before", []), state), _legs(data.get("after", []), state)
    legs = [k for k in store.LEG_ORDER if k in before or k in after]
    legs += sorted((set(before) | set(after)) - set(legs))
    rows = "".join(
        f"<h3>{html.escape(leg.replace('-', ' · '))}</h3><div class=\"pair\">"
        f"{_figure(folder, before.get(leg), 'before')}{_figure(folder, after.get(leg), 'after')}</div>"
        for leg in legs)
    if len(lines) > 1:
        heading = "<ul>" + "".join(f"<li>{html.escape(t)}</li>" for t in lines) + "</ul>"
    else:
        heading = f"<h2>{html.escape(lines[0]) if lines else 'Other captured state'}</h2>"
    return (f"<section>{heading}<div class=\"small\">state <code>{html.escape(state)}</code></div>"
            f"{rows}</section>")


def _lint_block(data: dict) -> str:
    linted = [r for r in data.get("after", []) if "findings" in r]
    if not linted:
        return '<p class="small">Layout lint: not run (no layout_lint manifest for the after capture).</p>'
    entries = [{"state": r.get("state"), "viewport": r.get("viewport"), "theme": r.get("theme"),
                "findings": r.get("findings")} for r in linted]
    errors = proof.failing_lines(entries)
    if not errors:
        return '<p class="ok">Layout lint: clean on every linted leg.</p>'
    return ('<p class="bad">Layout lint errors:</p><ul>'
            + "".join(f"<li><code>{html.escape(e)}</code></li>" for e in errors) + "</ul>")


def render(repo_name: str, pr: int, data: dict, folder: Path) -> str:
    """The self-contained page (inline CSS, `data:` images, no scripts)."""
    criteria: Dict[str, object] = data.get("criteria") or {}
    no_visual: List[str] = data.get("no_visual") or []
    states = {str(r.get("state")) for p in store.PHASES for r in data.get(p, [])}
    sections = [_state_section(folder, data, s, proof.criterion_lines(v)) for s, v in criteria.items()]
    sections += [_state_section(folder, data, s, []) for s in sorted(states - set(criteria))]
    sections += [f"<section><h2>{html.escape(t)}</h2><p class=\"small\">No visual change.</p></section>"
                 for t in no_visual]
    sha = data.get("merge_sha") or "not merged yet"
    issue = f" · issue #{data['issue']}" if data.get("issue") else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>Proof {html.escape(repo_name)}#{pr}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{html.escape(data.get('title') or f'{repo_name} PR #{pr}')}</h1>"
        f"<div class=\"meta\">{html.escape(repo_name)} PR #{pr}{issue} · merge <code>{html.escape(sha)}</code>"
        f" · built {html.escape(data.get('utc', ''))}</div>"
        + _lint_block(data) + "".join(sections) + "</main></body></html>"
    )


def build(repo: Path, repo_name: str, pr: int, criteria: Dict[str, List[str]], no_visual: List[str],
          title: str = "", issue: Optional[int] = None, sha: str = "") -> List[str]:
    """Write `.proof/<PR>/index.html` and its index row, stage a sendable copy; return KEY=value lines."""
    folder = store.prepare_pr_folder(repo, pr)
    if folder is None:
        return ["PROOF_PAGE=not_built reason=not a git checkout"]
    data = store.load_data(folder)
    if not (data.get("before") or data.get("after")):
        return [f"PROOF_PAGE=not_built reason=no captures for PR {pr} or this branch "
                "(`ui_proof capture-before` / `capture-after` first)"]
    if criteria:
        data["criteria"] = criteria
    elif not data.get("criteria"):
        data["criteria"] = (proof.load_record(repo) or {}).get("criteria") or {}
    if no_visual:
        data["no_visual"] = no_visual
    for key, value in (("title", title), ("issue", issue), ("merge_sha", sha)):
        if value:
            data[key] = value
    data["utc"] = store.utc_now()
    store.save_data(folder, data)
    page = folder / store.PAGE_NAME
    page.write_text(render(repo_name, pr, data, folder), encoding="utf-8")
    store.update_index(folder.parent, pr, {"title": data.get("title"), "issue": data.get("issue"),
                                           "merge_sha": data.get("merge_sha"), "utc": data["utc"],
                                           "branch": data.get("branch")})
    send_dir = Path(tempfile.gettempdir()).joinpath(*SEND_SUBDIR)
    send_dir.mkdir(parents=True, exist_ok=True)
    send_copy = send_dir / f"{store.safe_name(repo_name)}-{pr}.html"
    shutil.copyfile(page, send_copy)
    size = page.stat().st_size
    lines = [f"PROOF_PAGE={page}", f"KEY={pr}", f"SEND_PATH={send_copy}", f"BYTES={size}"]
    if not data.get("before"):
        lines.append("PROOF_WARNING=no before capture; run `capture-before` against main")
    if size > SEND_LIMIT_BYTES:
        lines.append(f"SEND_WARNING=over the {SEND_LIMIT_BYTES // (1024 * 1024)} MB outbox cap; "
                     "capture fewer legs or smaller states")
    if sha:
        lines += store.publish(repo, pr)
    return lines
