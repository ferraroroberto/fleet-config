"""The per-app classification leg of `/design-review fleet inventory`.

The comparator (`inventory.py`) lists what the DOM cannot decide under `unclassified`, each entry with a stable
`id`. This module turns one app's entries into a reader prompt and validates the reader's JSON answers. The
answers feed back into `inventory.compare(..., classified=...)`. Python spawns no agent: the skill hands each
prompt to one read-only reader.

Two questions, one per kind of entry:

  * an action entry (`inventory.ACTION_REASONS`): which action is this control? One of `ACTIONS`, or `none`
    when it is no action at all (a whole-row tap that only selects, a decorative glyph).
  * an option-count entry (`inventory.R_NO_OPTIONS`): how many options does this select or combobox offer, and
    is it a `select` (a fixed list) or a `combobox` (typed, with suggestions)?

The answer file:

    {"app": "<target>", "answers": [{"id": "u-<12 hex>", "action": "<name>" | "none"},
                                    {"id": "u-<12 hex>", "options": <int >= 1>, "kind": "select" | "combobox"}]}

Validation keeps every well-formed answer and drops the rest with a reason, the way the judge merge does: an
unknown id, a field that does not fit the entry's question, an action outside the vocabulary, an option count
that is not a positive integer, a second answer for one id. An unreadable file answers nothing. Nothing here
ever guesses an answer.

The prompt carries local screenshot paths and masked labels: it stays in the sweep directory, never on GitHub.

Public surface:

    ACTIONS                                    -> the answer vocabulary for an action entry
    entries_for(section, app)                  -> [unclassified entry]
    prompt(app, entries, screenshots, answers_path) -> markdown text
    validate(text, app, entries)               -> ({id: answer}, [problem])
    screenshots_of(metrics, run_dir)           -> {screen id: absolute screenshot path}

stdlib only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import inventory

# Every action name the comparator can produce, plus the DOM-decided ones a reader may also recognise.
ACTIONS: Tuple[str, ...] = tuple(sorted({name for name, _ in inventory.ACTION_VOCAB}
                                        | {"row open", "setting open", "open", "send glyph", "copy glyph"}))
NONE = "none"
CHOICE_KINDS = ("select", "combobox")


def entries_for(section: Optional[dict], app: str) -> List[dict]:
    """The app's unclassified entries, in the comparator's order."""
    return [u for u in (section or {}).get("unclassified") or [] if u.get("app") == app]


def screenshots_of(metrics: dict, run_dir: Path) -> Dict[str, str]:
    """Screen id -> its viewport screenshot's absolute path (metrics name it relative to `<run_dir>/shots/`)."""
    return {str(s["id"]): str(Path(run_dir) / "shots" / str(s["screenshot"])) for s in metrics.get("screens") or []
            if isinstance(s, dict) and s.get("id") and s.get("screenshot")}


def _question(u: dict) -> str:
    if u["reason"] == inventory.R_NO_OPTIONS:
        return 'how many options, and is it a fixed list or typed? Answer `{"id", "options": <n>, "kind": "select" | "combobox"}`'
    return 'which action is it? Answer `{"id", "action": "<one of the actions>"}`, or `"none"` when it is no action'


def prompt(app: str, entries: List[dict], screenshots: Dict[str, str], answers_path: str) -> str:
    """The reader prompt for one app: deterministic, no timestamp, local paths only."""
    lines = [
        f"# Classify `{app}`'s unclassified controls",
        "",
        "You are a read-only reader. Open each entry's screenshot and answer only what the page shows; edit nothing,",
        "run nothing but file reads, and do not guess: leave an entry out when the screenshot does not settle it.",
        "",
        f"Actions: {', '.join(f'`{a}`' for a in ACTIONS)}, or `{NONE}`.",
        "`destructive` deletes or resets; `dismiss` closes without saving; `commit` saves or sends; `open` opens a",
        "dialog or menu; `row open` goes to another screen; `overflow menu` is a kebab or More menu.",
        "",
        f"Write one JSON object to `{answers_path}` and nothing else:",
        "",
        "```json",
        json.dumps({"app": app, "answers": [{"id": "u-000000000000", "action": "commit"},
                                            {"id": "u-111111111111", "options": 7, "kind": "select"}]}, indent=2),
        "```",
        "",
        "## Entries",
        "",
    ]
    for u in entries:
        shot = screenshots.get(str(u.get("screen")))
        lines += [
            f"### `{u['id']}`",
            f"- control: {u['kind']} · component {u['component']} · label `{u['label'] or '(none)'}` · {u['n']} on screen",
            f"- why unclassified: {u['reason']}",
            f"- screen: {u['screen']} · screenshot: {shot or '(none captured: leave this entry out)'}",
            f"- question: {_question(u)}",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def _check(a: dict, entry: dict) -> Tuple[Optional[dict], Optional[str]]:
    """One answer against its entry: `(answer, None)` or `(None, why)`."""
    if entry["reason"] == inventory.R_NO_OPTIONS:
        if "action" in a:
            return None, "an option-count entry answered with an action"
        n, kind = a.get("options"), a.get("kind", entry["kind"])
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            return None, f"options {n!r} is not a positive integer"
        if kind not in CHOICE_KINDS:
            return None, f"kind {kind!r} is not one of {', '.join(CHOICE_KINDS)}"
        return {"options": n, "kind": kind}, None
    if "options" in a:
        return None, "an action entry answered with an option count"
    action = a.get("action")
    if action == NONE:
        return {"action": None}, None
    if action not in ACTIONS:
        return None, f"action {action!r} is not in the vocabulary"
    return {"action": action}, None


def validate(text: str, app: str, entries: List[dict]) -> Tuple[Dict[str, dict], List[str]]:
    """The valid answers keyed by entry id, and one problem line per dropped answer (ids only, no label)."""
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return {}, [f"unreadable answers: {exc.__class__.__name__}"]
    if not isinstance(doc, dict) or not isinstance(doc.get("answers"), list):
        return {}, ["answers is not an object with an `answers` list"]
    if doc.get("app") not in (None, app):
        return {}, [f"answers are for {doc.get('app')!r}, not {app!r}"]
    by_id = {u["id"]: u for u in entries}
    out: Dict[str, dict] = {}
    problems: List[str] = []
    for i, a in enumerate(doc["answers"]):
        uid = a.get("id") if isinstance(a, dict) else None
        if uid not in by_id:
            problems.append(f"answer {i}: unknown id {uid!r}")
            continue
        if uid in out:
            problems.append(f"answer {i}: a second answer for {uid}, the first one kept")
            continue
        answer, why = _check(a, by_id[uid])
        if answer is None:
            problems.append(f"answer {i} ({uid}): {why}")
            continue
        out[uid] = answer
    return out, problems
