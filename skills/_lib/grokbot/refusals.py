"""What the Grok Bot relay refuses to carry, as pure text checks.

Grok Bot runs its local commands on "Always allow", so no human tap stands between its model and a
launcher session. These checks are two of the three guards that remain (the sender tag and the skill
pack's never-do list are the others), and they run in the helper, before anything is sent.

Both are deliberately blunt: a false refusal costs one rephrased message, a false pass costs a leaked
credential or a discarded branch. A refusal names the family it matched, never the matched text.

The credential list repeats `hooks/_lib.SECRET_PATTERNS` because `hooks/` and `skills/_lib/` are
independent trees that never import each other; it adds the shapes a relayed message can carry that a
commit diff rarely does (a bare key assignment, a private-key block, a bearer token).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

SECRET_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("Slack token", r"\bxox[baprs]-[A-Za-z0-9]+-[A-Za-z0-9]+-[A-Za-z0-9]{8,}"),
    ("Telegram bot token", r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    ("API key (sk-)", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("xAI key", r"\bxai-[A-Za-z0-9_-]{20,}"),
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9_]{20,}"),
    ("AWS access key id", r"\bAKIA[0-9A-Z]{16}"),
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("JSON web token", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("bearer token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    ("credential assignment",
     r"(?i)\b(?:api[_-]?key|secret|token|passw(?:or)?d|auth[_-]?key)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;]{8,}"),
)

# Each family is the owner-only list from the dispatch rules: discard work, delete or adopt branches,
# tear down worktrees, force-push.
DESTRUCTIVE_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("force-push", r"(?i)\bgit\s+push\b[^\n]*(?:--force\b|--force-with-lease\b|\s-f\b|\s\+[\w/.-]+)"),
    ("force-push", r"(?i)\bforce[- ]?push"),
    ("discard work", r"(?i)\bgit\s+(?:reset\s+--hard|checkout\s+--\s|restore\s+\.|clean\s+-\w*f|stash\s+(?:drop|clear))"),
    ("discard work",
     r"(?i)\bdiscard\s+(?:all\s+|the\s+|that\s+|this\s+|his\s+|her\s+|its\s+|your\s+)*(?:uncommitted\s+)?(?:work|changes|edits|commits)\b"),
    ("delete or adopt a branch", r"(?i)\bgit\s+branch\s+(?:-D\b|-d\b|--delete\b)"),
    ("delete or adopt a branch", r"(?i)\bgit\s+push\s+\S+\s+(?:--delete\b|:[\w/.-]+)"),
    ("delete or adopt a branch",
     r"(?i)\b(?:delete|remove|drop|adopt)\s+(?:all\s+|the\s+|that\s+|this\s+|your\s+|remote\s+|local\s+|its\s+|stale\s+|merged\s+)*branch(?:es)?\b"),
    ("tear down a worktree", r"(?i)\bgit\s+worktree\s+(?:remove|prune)\b"),
    ("tear down a worktree",
     r"(?i)\b(?:tear\s*down|remove|delete|prune)\s+(?:all\s+|the\s+|that\s+|this\s+|your\s+|its\s+|stale\s+|finished\s+)*worktrees?\b"),
    ("recursive delete", r"(?i)\brm\s+-[a-z]*r[a-z]*f?|\bremove-item\b[^\n]*-recurse"),
)


def first_hit(text: str, patterns: Tuple[Tuple[str, str], ...]) -> Optional[str]:
    """The label of the first family that matches `text`, else None."""
    for label, pattern in patterns:
        if re.search(pattern, text):
            return label
    return None


def secret_family(text: str) -> Optional[str]:
    return first_hit(text, SECRET_PATTERNS)


def destructive_family(text: str) -> Optional[str]:
    return first_hit(text, DESTRUCTIVE_PATTERNS)


def redact_secrets(text: str) -> Tuple[str, int]:
    """`text` with every credential-shaped run replaced by `[redacted]`, and how many were replaced."""
    count = 0
    for _label, pattern in SECRET_PATTERNS:
        text, n = re.subn(pattern, "[redacted]", text)
        count += n
    return text, count
