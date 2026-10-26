"""The census's per-file definitions: which files count, and how each one is measured.

These are the definitions of the 2026-10-22 step-1 census, kept unchanged so that a
re-run compares like with like:

- A *code file* has one of `CODE_EXT` and sits under no `EXCLUDED_DIRS` folder (or any
  `_vendored*` folder), and is not minified, bundled, a lock file or generated.
- A file's *lines* are its newline count, plus one for a last line with no newline.
- A Python function spans `lineno`..`end_lineno` from `ast`; nested functions and
  methods count on their own as well as inside their outer function.
- A JS/TS function spans from its opening line to the first later line at the same or
  a lower indent, which must open with `}`. A function whose closer cannot be found is
  skipped, never guessed; spans under 20 lines are not recorded.

Flat directories and `src`->`app` imports use the structure ratchet's own definitions
(`scripts/structure_ratchet.py`), so the census and the gate agree on what they count.
"""

from __future__ import annotations

import ast
import bisect
import re
import sys
import warnings
from pathlib import Path, PurePosixPath
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import structure_ratchet  # noqa: E402

CODE_EXT = frozenset({
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".ps1", ".psm1",
    ".bat", ".cmd", ".sh", ".css", ".scss", ".html", ".sql", ".go", ".rs", ".swift", ".kt",
})
JS_EXT = frozenset({".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"})
EXCLUDED_DIRS = frozenset({
    "_vendored", "vendor", "vendored", "node_modules", "third_party", "dist", "build",
    "site-packages", ".venv", "venv", "__pycache__", "weights", "fixtures", "__snapshots__",
    "generated",
})
_EXCLUDED_NAME = re.compile(r"(\.min\.(js|css)$|\.bundle\.js$|-lock\.|\.lock$|_pb2\.py$|\.generated\.)")
_TEST_PATH = re.compile(r"(^|/)(tests?|e2e)/|(^|/)test_[^/]+\.py$|_test\.py$|\.(spec|test)\.(js|ts)$")

# A JS function opener: `function name(...) {`, `name = (...) => {` / `name: x => {`, or a
# method `name(...) {` at the start of a line.
_JS_FUNC = re.compile(
    r"(?:function\s*\*?\s*([A-Za-z_$][\w$]*)?\s*\([^)]*\)\s*\{)"
    r"|(?:([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*\{)"
    r"|(?:^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{)", re.M)
_JS_KEYWORDS = frozenset({"if", "for", "while", "switch", "catch", "with", "return", "function"})
_JS_MIN_SPAN = 20

LAYER_LOWER, LAYER_UPPER = "src", "app"


def _parse(text: str) -> Optional[ast.Module]:
    """The module's tree, or None when it does not parse; a measured repo's own warnings are not ours to print."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        try:
            return ast.parse(text)
        except (SyntaxError, ValueError):
            return None


def is_code(path: str) -> bool:
    """True when `path` is a code file the census counts."""
    pure = PurePosixPath(path)
    if pure.suffix.lower() not in CODE_EXT:
        return False
    if any(part.lower() in EXCLUDED_DIRS or part.startswith("_vendored") for part in pure.parts[:-1]):
        return False
    return not _EXCLUDED_NAME.search(path)


def is_test(path: str) -> bool:
    """True when `path` is a test file (a tests/ or e2e/ folder, or a test_/spec/test name)."""
    return bool(_TEST_PATH.search(path))


def kind(path: str) -> str:
    """`test` for a test file, else the bare extension (`py`, `css`, ...)."""
    return "test" if is_test(path) else PurePosixPath(path).suffix.lower().lstrip(".")


def count_lines(text: str) -> int:
    """Newlines, plus one for a last line that has none."""
    return text.count("\n") + (0 if not text or text.endswith("\n") else 1)


def python_functions(text: str, path: str) -> Optional[list[tuple[int, str]]]:
    """`(lines, "path:line name")` for every function, or None when the file does not parse."""
    tree = _parse(text)
    if tree is None:
        return None
    return [
        (node.end_lineno - node.lineno + 1, f"{path}:{node.lineno} {node.name}")
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def js_functions(text: str, path: str) -> list[tuple[int, str]]:
    """`(lines, "path:line name")` for every JS/TS function whose closing line is found."""
    lines = text.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    found = []
    for match in _JS_FUNC.finditer(text):
        name = next((g for g in match.groups() if g), "<anon>")
        if name in _JS_KEYWORDS:
            continue
        first = bisect.bisect_right(starts, match.start()) - 1
        if not lines[first].rstrip().endswith("{"):
            continue
        indent = len(lines[first]) - len(lines[first].lstrip())
        end = None
        for j in range(first + 1, len(lines)):
            line = lines[j]
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                end = j if line.lstrip().startswith("}") else None
                break
        if end is not None and end - first + 1 >= _JS_MIN_SPAN:
            found.append((end - first + 1, f"{path}:{first + 1} {name}"))
    return found


def functions(path: str, text: str) -> Optional[list[tuple[int, str]]]:
    """Function spans for a Python or JS/TS file, [] for other kinds, None for unparseable Python."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".py":
        return python_functions(text, path)
    if suffix in JS_EXT:
        return js_functions(text, path)
    return []


def flat_dirs(paths: list[str], limit: int = structure_ratchet.DEFAULT_MAX_DIR_MODULES) -> list[tuple[str, int]]:
    """`(directory, modules)` for each directory holding more than `limit` modules directly."""
    return [(f.key.split(":", 1)[1], f.value) for f in structure_ratchet.dir_findings(paths, limit)]


def layer_imports(path: str, text: str) -> list[str]:
    """Modules of `app` that a `src/**` Python file imports (empty outside `src/` or when it does not parse)."""
    if not (path.startswith(LAYER_LOWER + "/") and path.endswith(".py")):
        return []
    tree = _parse(text)
    if tree is None:
        return []
    return sorted({
        name for name in structure_ratchet.python_imports(tree)
        if name == LAYER_UPPER or name.startswith(LAYER_UPPER + ".")
    })
