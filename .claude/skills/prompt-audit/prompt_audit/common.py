"""Paths, fleet constants, the `skills/_lib` imports and small pure helpers.

Part of the `prompt_audit` package (fleet-config#931); see `__init__.py`.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tomllib
from pathlib import Path
from typing import Optional


SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "skills" / "_lib"))
import git_run  # noqa: E402
from audit_issue import rubric_sha  # noqa: E402
from audit_issue_client import run_audit_issue  # noqa: E402
from fleet_repo_scan import fleet_repos, is_linked_worktree  # noqa: E402
from frontmatter import frontmatter_error  # noqa: E402
from skill_description import frontmatter_description, prose_words, strip_quoted  # noqa: E402
from utf8_stdio import ensure_utf8_stdio  # noqa: E402

SOURCES_TOML = SKILL_DIR / "sources.toml"
RULES_MD = SKILL_DIR / "rules.md"
# Repo-relative, never the ~/.claude junction (fleet-config#502): a run from a
# `-wt-N` worktree must drive its own checkout's helper.
AUDIT_ISSUE = REPO_ROOT / "skills" / "_lib" / "audit_issue.py"

HOME_REPO = "fleet-config"  # membership key whose global file + skill tiers are scanned
MASTER_REPO = "project-scaffolding"
LITE_REPO = "fleet-config-lite"
LITE_GLOBAL = "global-instructions.md"
LEDGER_REPO = "ferraroroberto/fleet-config"
KIND = "prompt-audit"
TITLE = "prompt-audit ledger"
BLOCK_MARKER = "<!-- prompt-audit-ledger -->"
LEDGER_CAP = 600  # entries; overflow is simply rescanned next run (the safe direction)
DIGEST_FINDINGS_CAP = 200
COMMENT_CHAR_CAP = 60000  # GitHub rejects bodies over 65536
STAMP_PREFIX = "prompt-audit-digest"  # read by delivery_check.py
NEUTRAL = "neutral"
VERDICTS = ("unchanged", "changed", "new-guide", "not-checked")

# kind caps for R-14 — (unit, limit)
SIZE_CAPS = {"claude-md": ("lines", 200), "rules": ("lines", 200),
             "skill": ("body-lines", 500), "agents-md": ("bytes", 32768)}
ALWAYS_ON_KINDS = {"claude-md", "agents-md", "rules"}


# ---- small pure helpers ---------------------------------------------------------

def sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def clean(value: str) -> str:
    """A value safe inside a `KEY=value|...` line."""
    return re.sub(r"\s+", " ", value.replace("|", "/")).strip()


def rules_rubric(data: Optional[bytes] = None) -> str:
    """The ledger's rubric: sha256 of `rules.md` with line endings normalised.

    `core.autocrlf` checks the same commit out as LF in one checkout and CRLF in
    another; hashing raw bytes would read a checkout change as a rule-set edit and
    force a pointless full rescan.
    """
    raw = RULES_MD.read_bytes() if data is None else data
    return rubric_sha(raw.replace(b"\r\n", b"\n"))


def load_toml(path: Path = SOURCES_TOML) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


# Shared by inventory (section scanning) and lint (fence-aware detectors).
_FENCE = re.compile(r"^\s*(```|~~~)")
