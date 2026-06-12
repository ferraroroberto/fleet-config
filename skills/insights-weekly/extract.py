"""Strip a Claude Code Insights report HTML down to clean, diff-friendly text.

The ``/insights`` slash command writes a self-contained, dated
``report-<timestamp>.html`` (heavy inline CSS) into ``~/.claude/usage-data/``.
For a week-over-week comparison we only care about the *content* — the headline
stats and each section's prose — not the styling. This emits readable markdown
so two reports can be diffed by what they say, not by their (noisy, restyled)
markup.

Stdlib only; no dependencies. Usage::

    py extract.py <path-to-report.html>      # -> markdown on stdout

Exit codes: 0 ok, 1 file not found, 2 bad usage.
"""

from __future__ import annotations

import sys
from html.parser import HTMLParser
from pathlib import Path

# Piped/redirected stdout falls back to cp1252 on Windows, which chokes on the
# report's em-dashes and arrows — force UTF-8 (global Windows-capture gotcha).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
    pass

# Whole subtrees we never want in the text: styling, scripts, document head, and
# the table-of-contents nav (it only duplicates the section headings).
SKIP_TAGS = {"style", "script", "head", "nav"}
# Block-level tags whose boundaries should break the text into separate lines.
BLOCK_TAGS = {
    "div", "p", "section", "li", "ul", "ol", "tr", "br",
    "h1", "h2", "h3", "h4",
}
HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}


class ReportExtractor(HTMLParser):
    """Flatten report HTML to markdown: headings as ``#``-prefixed lines, every
    other text run on its own line, all tags and inline CSS dropped."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._heading_level: int | None = None
        self._buf: list[str] = []

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if not text:
            self._heading_level = None
            return
        if self._heading_level:
            self.parts.append("\n" + "#" * self._heading_level + " " + text)
        else:
            self.parts.append(text)
        self._heading_level = None

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._flush()
        if tag in HEADING_TAGS:
            self._heading_level = HEADING_TAGS[tag]

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._buf.append(data)

    def get_markdown(self) -> str:
        self._flush()
        return "\n".join(p for p in self.parts if p.strip()).strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: extract.py <report.html>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1
    parser = ReportExtractor()
    parser.feed(path.read_text(encoding="utf-8"))
    sys.stdout.write(parser.get_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
