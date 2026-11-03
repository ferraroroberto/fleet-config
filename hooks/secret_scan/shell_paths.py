"""Where a path operand in a shell command points, as far as a hook can know.

Shared by both `secret_scan_guard` sinks: the publish sink reads the body file a
`gh` command names, the read sink checks the file a printing command names, and
both follow a chain's leading `cd`/`Set-Location`/`pushd` the same way.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import _lib

# Segment verbs that move the directory a later relative operand resolves
# against. A push moves it like a cd; a pop makes the base unknown.
CD_VERBS = _lib.CD_VERBS | _lib.PUSH_VERBS
POP_VERBS = _lib.POP_VERBS


def cd_target(words: List[str]) -> Optional[str]:
    """The one literal directory operand of a `cd`-like segment, else ``None``.

    Option words (`-P`, `-Path`, `-LiteralPath`, …) are skipped; zero or several
    remaining operands, or `cd -`, name no directory we can follow.
    """
    operands = [w for w in words[1:] if not w.startswith("-")]
    return operands[0] if len(operands) == 1 else None


def resolve(raw: str, base: Optional[Path]) -> Optional[Path]:
    """``raw`` as a path, or ``None`` when the hook cannot know where it points.

    `-` is stdin; a `$VAR`, `$(…)` or backtick operand is expanded by the shell
    only after the hook has run; a relative path under an unknown base (after
    `cd $X`) has nowhere to resolve.
    """
    text = raw.strip()
    if not text or text == "-" or "$" in text or "`" in text or text.startswith("("):
        return None
    path = Path(_lib.msys_to_drive(text)).expanduser()
    if path.is_absolute():
        return path
    return base / path if base is not None else None


def follow_cd(words: List[str], current: Optional[Path]) -> Optional[Path]:
    """The base after a `cd`-like (or pop) segment whose words are ``words``."""
    if words[0].lower() in POP_VERBS:
        return None
    target = cd_target(words)
    return resolve(target, current) if target is not None else None
