"""Check that a PR body carries the `Merge danger:` line.

The line names the door (one-way or two-way) and a one-word blast radius, so the owner reads how
reversible a merge is without opening the diff. A body without a well-formed line is refused with
exit 1. Terms are defined in docs/skills/merge-danger.md. stdlib only.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import List, Optional

LINE_RE = re.compile(
    r"^\*\*Merge danger:\*\*\s+(?P<door>one-way|two-way) door\s+·\s+blast radius:\s+(?P<radius>[A-Za-z][\w-]*)"
    r"(?:\s+·\s+(?P<reason>\S.*))?\s*$",
    re.MULTILINE,
)


def find_line(body: str) -> Optional[str]:
    """The first well-formed line, or None. A one-way door must state its reason."""
    for match in LINE_RE.finditer(body):
        if match.group("door") == "one-way" and not match.group("reason"):
            continue
        return match.group(0).strip()
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("body_file", help="PR body file, or - for stdin")
    args = parser.parse_args(argv)
    if args.body_file == "-":
        body = sys.stdin.read()
    else:
        with open(args.body_file, encoding="utf-8") as handle:
            body = handle.read()
    line = find_line(body)
    if line is None:
        print("MERGE_DANGER=MISSING reason=no well-formed `**Merge danger:** two-way door · blast radius: <word>` line")
        return 1
    print(f"MERGE_DANGER={line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
