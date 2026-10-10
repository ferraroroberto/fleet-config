"""Set or clear the owner's title on one captured conversation (fleet-config#1348).

The shown name of a conversation is otherwise its digest ``topic``, which the
indexer rewrites whenever the capture changes. A title set here lives in the
conversations dir's ``titles.json`` sidecar (see
``conversation_index.TITLES_NAME`` for why a sidecar), so it survives every
re-index path, while the topic keeps updating underneath it. ``index.json``
carries it as ``title`` and search matches it at digest weight.

One call does the whole job, so a caller never sees a half-applied rename:
validate the capture exists, write the sidecar atomically, re-render
``index.json`` and bring the search db up to date.

Callers serialise their own calls: two concurrent writers to the same dir can
each read the sidecar before the other writes it, and the later write wins.

Usage (invoke the resolved Python path directly — a bare ``py``/``python`` is
not reliably on ``PATH`` here; see ``_lib.find_python_executable``)::

    …/python.exe hooks/conversation_title.py --cwd E:/automation/life-os --skill <skill> --file <capture>.md --title "New name"
    …/python.exe hooks/conversation_title.py --project life-os --skill <skill> --file <capture>.md --clear

``--skill`` is the ``skill`` label an ``index.json`` row carries. On success it
prints one JSON object, ``{"skill", "file", "title"}`` (``title`` is ``""`` once
cleared), and exits 0. Refusals print one line to stderr: exit 1 project not
found / not opted in, 2 invalid input (argparse's own code), 3 no such
skill/conversation, 4 the sidecar could not be read or a file written.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib  # noqa: E402
import conversation_index as ci  # noqa: E402
import conversation_search  # noqa: E402
from conversation_capture import CaptureConfig  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

logger = logging.getLogger("conversation_title")

MAX_TITLE_CHARS = 120


class ConversationNotFound(LookupError):
    """The named skill or capture does not exist."""


def capture_exists(conv_dir: Path, file: str) -> bool:
    """True when ``file`` is a capture in ``conv_dir`` or its ``archive/``.

    ``file`` must be a bare ``.md`` filename: anything with a path part is
    rejected rather than resolved, so a caller can never name a file outside
    the conversations dir.
    """
    if Path(file).name != file or file in ("", ".", "..") or not file.endswith(".md"):
        return False
    if file == ci.INDEX_NAME:
        return False
    return (conv_dir / file).is_file() or (conv_dir / "archive" / file).is_file()


def normalise_title(title: str) -> str:
    """Collapse whitespace (newlines included) to single spaces; ``""`` clears."""
    clean = " ".join(title.split())
    if len(clean) > MAX_TITLE_CHARS:
        raise ValueError(f"title is longer than {MAX_TITLE_CHARS} characters")
    return clean


def _write_titles(conv_dir: Path, titles: "dict[str, str]") -> None:
    """Atomically replace the sidecar. Raises ``OSError`` on failure."""
    path = conv_dir / ci.TITLES_NAME
    _lib.sweep_stale_atomic_temps(path)
    temporary: Optional[str] = None
    try:
        fd, temporary = tempfile.mkstemp(
            dir=str(conv_dir), prefix=_lib.atomic_tmp_prefix(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(sorted(titles.items())), handle, indent=2, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        raise


def set_title(conv_dir: Path, label: str, file: str, title: str) -> str:
    """Set (or, with an empty ``title``, clear) one conversation's title.

    Writes the sidecar, then re-renders ``index.json`` so the list shows the
    change without waiting for the next index run. Returns the stored title.

    Raises ``ConversationNotFound`` when setting a title on a capture that does
    not exist, ``ValueError`` on an invalid title, ``OSError`` when the sidecar
    could not be read or a file could not be written. Clearing never requires the
    capture to exist, so a caller can drop the title of a conversation it is
    deleting.
    """
    clean = normalise_title(title)
    if clean and not capture_exists(conv_dir, file):
        raise ConversationNotFound(f"no conversation {file!r} in {label}")
    try:
        titles = ci.read_titles(conv_dir, strict=True)
    except ValueError as exc:  # the sidecar, not the caller's input, is at fault
        raise OSError(str(exc)) from exc
    if titles.get(file, "") == clean:
        return clean
    if clean:
        titles[file] = clean
    else:
        titles.pop(file, None)
    _write_titles(conv_dir, titles)
    logger.info("ℹ️ title %s for %s in %s", "set" if clean else "cleared", file, label)
    index_md = conv_dir / ci.INDEX_NAME
    if index_md.exists() and not ci.write_index_json(conv_dir, label, ci.parse_index(index_md)):
        raise OSError(f"title saved but {ci.INDEX_JSON_NAME} could not be refreshed")
    return clean


def conversations_dir_for(cfg: CaptureConfig, label: str) -> Optional[Path]:
    """The conversations dir behind an ``index.json`` ``skill`` label."""
    return next((d for d, lbl in ci.conversations_dirs(cfg) if lbl == label), None)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    ap = argparse.ArgumentParser(description="Set or clear a conversation's title.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--project", help="project name from projects.toml")
    g.add_argument("--cwd", help="resolve the project by a cwd path")
    ap.add_argument("--skill", required=True, help="the index.json `skill` label")
    ap.add_argument("--file", required=True, help="the capture's filename")
    t = ap.add_mutually_exclusive_group(required=True)
    t.add_argument("--title", help="the new title (empty clears)")
    t.add_argument("--clear", action="store_true", help="remove the title")
    args = ap.parse_args()

    cfg = conversation_search.resolve_config(args.project, args.cwd)
    if cfg is None:
        print("project not found or not opted into capture", file=sys.stderr)
        return 1
    conv_dir = conversations_dir_for(cfg, args.skill)
    if conv_dir is None or not conv_dir.is_dir():
        print(f"no conversations for skill {args.skill!r}", file=sys.stderr)
        return 3
    try:
        stored = set_title(conv_dir, args.skill, args.file, "" if args.clear else args.title)
    except ConversationNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"could not save the title: {exc}", file=sys.stderr)
        return 4
    # The db is a pure derivative and fail-open: a failed sync only delays the
    # title reaching search until the next index run, which re-syncs anyway.
    conversation_search.sync(cfg)
    print(json.dumps({"skill": args.skill, "file": args.file, "title": stored}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
