"""Mechanical check that an audit finding quotes the line it is about (fleet-config#960 A).

`/codebase-audit` requires every finding to cite a real `file:line`, but a
`file:line` can be hallucinated and still look valid. So a finding now carries
a short verbatim quote of the motivating line(s), and this helper confirms the
quoted text exists in the named file before the finding is filed. That matters
most in the unattended `/audit-fleet` run, where nobody reads the findings
before they are upserted.

A finding line is a checklist item in the bucket-issue grammar:

    - [ ] **path/to/file.py:42** — what's wrong. Quote: `the verbatim text`. Fix: …

Verdicts per finding, one line each:

  VERIFIED    the quote occurs in the file (whitespace-normalized, so CRLF,
              trailing blanks and re-indentation don't matter)
  MISMATCH    the file was read and the quote is not in it
  NO_QUOTE    the finding carries no `Quote:` segment
  UNREADABLE  the file could not be read: could not establish, never a pass

Only VERIFIED may be filed. `check` exits 0 only when every finding it looked
at is VERIFIED. Ticked (`[x]`) items and items tagged `_(carried …)_` (not
re-verified this run, so no new claim) are never checked. This helper judges
no content beyond "does this text exist there"; `audit_issue.py` stays the
identity-only upsert.

stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, NamedTuple, Optional

_FINDING_RE = re.compile(r"^\s*-\s\[(?P<box>[ xX])\]\s\*\*(?P<path>[^*]+?):(?P<line>\d+)(?:-\d+)?\*\*(?P<rest>.*)$")
_QUOTE_RE = re.compile(r"Quote:\s*`(?P<q>[^`]+)`")


class Verdict(NamedTuple):
    path: str
    line: int
    status: str  # VERIFIED | MISMATCH | NO_QUOTE | UNREADABLE
    detail: str = ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def check_body(body: str, repo_path: Path, *, include_ticked: bool = False) -> List[Verdict]:
    """A verdict for every unticked finding line in an audit-issue body."""
    cache: dict[str, Optional[str]] = {}
    out: List[Verdict] = []
    for raw in body.splitlines():
        m = _FINDING_RE.match(raw)
        if not m or (m.group("box") != " " and not include_ticked):
            continue
        if "_(carried" in m.group("rest"):
            continue  # not re-verified this run: a carried item makes no new claim
        path, line = m.group("path").strip(), int(m.group("line"))
        q = _QUOTE_RE.search(m.group("rest"))
        if not q:
            out.append(Verdict(path, line, "NO_QUOTE"))
            continue
        if path not in cache:
            try:
                cache[path] = _norm((repo_path / path).read_text(encoding="utf-8", errors="strict"))
            except (OSError, UnicodeDecodeError) as exc:
                cache[path] = None
                out.append(Verdict(path, line, "UNREADABLE", type(exc).__name__))
                continue
        text = cache[path]
        if text is None:
            out.append(Verdict(path, line, "UNREADABLE"))
        elif _norm(q.group("q")) in text:
            out.append(Verdict(path, line, "VERIFIED"))
        else:
            out.append(Verdict(path, line, "MISMATCH"))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="verify every unticked finding's quote against the repo")
    c.add_argument("--repo-path", required=True)
    c.add_argument("--body-file", required=True)
    args = ap.parse_args(argv)
    try:
        body = Path(args.body_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"QUOTES=unknown reason=body unreadable ({type(exc).__name__})")
        return 2
    verdicts = check_body(body, Path(args.repo_path))
    for v in verdicts:
        print(f"{v.status} {v.path}:{v.line}" + (f" ({v.detail})" if v.detail else ""))
    bad = [v for v in verdicts if v.status != "VERIFIED"]
    print(f"QUOTES={'ok' if not bad else 'unverified'} checked={len(verdicts)} unverified={len(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
