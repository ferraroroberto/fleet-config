"""Build-identity footer contract: one vendored `page-foot`, no private copy.

Every web app shows one `Build: <sha> · <time>` line from project-scaffolding's
`_vendored/page-foot` component, so "the tray restarted" and "the new build is
live" can be told apart. The scaffold-side `vendored` lens only sees an app
that carries the component, so an app with no footer at all, or with its own
hand-rolled line, never reaches it; this contract reads the app instead.

Severity follows the other adoption contracts (`text-size`, `range-tab`): an
app that has not adopted the footer yet WARNs, so a weekly `/design-sync`
reports it without a FAIL flood while each app adopts in its own issue. A
duplicate that sits next to the vendored footer FAILs, because adopting it
means deleting the private copy and a leftover is a plain defect.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Tuple

from ..css import strip_comments
from ..files import is_third_party, read_text, rel
from ._ctx import _ContractsCtx, _result

# The footer element's class, as a whole token: the vendored `class="page-foot"` and a JS-built
# `className = 'page-foot'` match, `_vendored/page-foot/page-foot.css` does not.
_MOUNT_RE = re.compile(r"(?<![\w/.-])page-foot(?![\w/.-])")
# A private copy of the shared formatter, or of its output text. The build line is `Build: ` followed by a
# concatenation, template or tag; a literal fallback written into the vendored element (`'Build: unknown'`) is not
# a second line, so the pattern needs the opening quote or `${` right after the colon.
_PRIVATE_FORMATTER_RE = re.compile(r"\b(?:function\s+fmtBuildTime\b|(?:const|let|var)\s+fmtBuildTime\b)")
_BUILD_LINE_RE = re.compile(r"""(?<![\w-])Build:\s*(?:["'`]\s*\+|\$\{|<)""")
_SHARED_FORMATTER = "buildReadoutText"

_Hit = Tuple[str, int]


def _app_markup(ctx: _ContractsCtx) -> List[Tuple[Path, str]]:
    """The app's own HTML/JS with comments stripped, minus the vendored `page-foot` folder itself."""
    out: List[Tuple[Path, str]] = []
    for path in ctx.html_files + ctx.js_files:
        # Any `_vendored/page-foot/` copy, so a second front end's vendored root is skipped too.
        if is_third_party(path) or {"_vendored", "page-foot"} <= set(Path(rel(ctx.root, path)).parts):
            continue
        out.append((path, strip_comments(read_text(path), "html" if path.suffix == ".html" else "js")))
    return out


def _hits(files: List[Tuple[Path, str]], pattern: "re.Pattern[str]", root: Path) -> List[_Hit]:
    found: List[_Hit] = []
    for path, text in files:
        for m in pattern.finditer(text):
            found.append((rel(root, path), text.count("\n", 0, m.start()) + 1))
    return found


def _first(hits: List[_Hit]) -> Optional[str]:
    return f"{hits[0][0]}:{hits[0][1]}" if hits else None


def _check_page_foot(ctx: _ContractsCtx) -> List[dict]:
    if not ctx.index_files:
        return [_result("page-foot", "NA", "no index.html found")]
    vendored = bool(ctx.vendored_root and (ctx.vendored_root / "page-foot" / "page-foot.css").is_file())
    files = _app_markup(ctx)
    mounts = _hits(files, _MOUNT_RE, ctx.root)
    # `buildReadoutText` anywhere in the app's own code is the vendored formatter's call site.
    uses_formatter = bool(_hits(files, re.compile(_SHARED_FORMATTER), ctx.root))
    private = _hits(files, _PRIVATE_FORMATTER_RE, ctx.root) + _hits(files, _BUILD_LINE_RE, ctx.root)

    if not vendored:
        detail = ("no _vendored/page-foot — every web app shows the one build footer; vendor it "
                  "verbatim from project-scaffolding and follow its first-time adopter checklist")
        if private:
            detail += (f"; a hand-rolled build line is in {_first(private)} "
                       "(delete it once the vendored footer is mounted)")
        return [_result("page-foot", "WARN", detail, _first(private))]

    problems: List[str] = []
    fails: List[str] = []
    evidence: Optional[str] = None
    if private:
        fails.append(f"hand-rolled build line or formatter beside the vendored footer "
                     f"({len(private)} site(s)) — delete it, buildReadoutText() is the one formatter")
        evidence = _first(private)
    if len(mounts) > 1:
        fails.append(f"{len(mounts)} page-foot elements — mount one footer, outside the tab panels")
        evidence = evidence or _first(mounts[1:])
    if not mounts:
        problems.append("vendored but never mounted — no <footer class=\"page-foot\"> in the app markup")
    elif not uses_formatter:
        problems.append("footer mounted but buildReadoutText() is never called to fill it")
    if fails:
        return [_result("page-foot", "FAIL", "; ".join(fails + problems), evidence)]
    if problems:
        return [_result("page-foot", "WARN", "; ".join(problems), _first(mounts))]
    return [_result("page-foot", "PASS", "one vendored page-foot footer, filled by buildReadoutText()", _first(mounts))]
