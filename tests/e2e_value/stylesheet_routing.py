from _suite import check, v
from routing_report import rr


# ---- stylesheet-aware routing (fleet-config#1033) ------------------------------------------------------
# A sheet-aware classifier (project-scaffolding#289's shape, reduced): `changed_selectors` diffs two
# texts line by line, any line holding `UNSAFE` poisons the sheet, and `classify` narrows a diff to the
# one surface whose selector prefixes own every changed rule. Real git history supplies the blobs.

SHEET_CLASSIFIER = '''
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Surface:
    name: str
    prefixes: tuple

    def owns_selector(self, sel):
        return any(sel.startswith(p) for p in self.prefixes)


@dataclass
class Config:
    surfaces: list
    shared_stylesheets: tuple = ()
    source: str = "declared"
    rules: list = field(default_factory=list)


@dataclass
class Routing:
    tier: str
    surface: str = ""


def load_config(path):
    text = Path(path).read_text()
    sheets = ("static/styles.css",) if "sheets" in text else ()
    board = (".board-",) if "selectors" in text else ()
    return Config([Surface("board", board), Surface("chat", (".chat-",) if board else ())], sheets)


def _classify_one(path, rules):
    return Category.FULL, "static"


class Category:
    FULL = type("C", (), {"name": "FULL"})()


def changed_selectors(old, new):
    if old is None or new is None:
        return None
    a, b = set(old.splitlines()), set(new.splitlines())
    diff = (a ^ b) - {""}
    if any("UNSAFE" in line for line in diff):
        return None
    return {line.split("{")[0].strip() for line in diff}


def classify(paths, config, sheet_changes=None):
    for sheet, sels in (sheet_changes or {}).items():
        if sheet not in config.shared_stylesheets or not sels:
            return Routing("full")
        owners = {s.name for sel in sels for s in config.surfaces if s.owns_selector(sel)}
        if len(owners) == 1 and all(any(s.owns_selector(sel) for s in config.surfaces) for sel in sels):
            return Routing("surface", owners.pop())
    return Routing("full")
'''

import subprocess  # noqa: E402

from git_fixtures import init_repo  # noqa: E402

sr = init_repo(empty_commit=False)


def _git(*args):
    return subprocess.run(["git", "-C", str(sr), *args], capture_output=True, text=True, check=True).stdout.strip()


(sr / "scripts").mkdir()
(sr / "static").mkdir()
(sr / "scripts" / "classify_e2e.py").write_text(SHEET_CLASSIFIER, encoding="utf-8")
(sr / ".fleet.toml").write_text("sheets\n", encoding="utf-8")
(sr / "proposed.toml").write_text("sheets selectors\n", encoding="utf-8")
css = sr / "static" / "styles.css"
css.write_text(".board-card { color: red; }\n.chat-row { color: blue; }\n", encoding="utf-8")
_git("add", "-A")
_git("commit", "-q", "-m", "base")
css.write_text(".board-card { color: green; }\n.chat-row { color: blue; }\n", encoding="utf-8")
_git("commit", "-qam", "board rule")
board_sha = _git("rev-parse", "HEAD")
css.write_text(".board-card { color: green; }\n.chat-row { color: blue; }\n.misc { margin: 0; }\n", encoding="utf-8")
_git("commit", "-qam", "unmapped rule")
misc_sha = _git("rev-parse", "HEAD")
css.write_text(".board-card { color: green; }\n.chat-row { color: blue; }\n.misc { margin: 0; }\n/* UNSAFE */\n",
               encoding="utf-8")
_git("commit", "-qam", "unsafe")
unsafe_sha = _git("rev-parse", "HEAD")
sheet_prs = [
    {"number": 11, "files": ["static/styles.css"], "mergeCommit": board_sha},
    {"number": 12, "files": ["static/styles.css"], "mergeCommit": misc_sha},
    {"number": 13, "files": ["static/styles.css"], "mergeCommit": unsafe_sha},
    {"number": 14, "files": ["static/styles.css"], "mergeCommit": ""},
    {"number": 15, "files": ["static/styles.css"], "mergeCommit": "0" * 40},
]
sx = v.routing_report(sr, pr_list=sheet_prs, proposed_path=sr / "proposed.toml")
check(sx["status"] == "ok" and sx["counterfactual"]["changed"] == [{"pr": 11, "from": "full", "to": "surface", "surface": "board"}],
      f"--proposed shows a sheet PR whose changed rules one surface owns narrowing -- {sx.get('counterfactual')}")
check(sx["sheet_routing"] == {"sheets": ["static/styles.css"], "reasons": {"static/styles.css": {
          "owned by one surface": 1, "unmapped selector": 1, "unsafe": 1, "unreadable": 2}}},
      f"per-sheet reason buckets; no merge commit and an object missing from the clone are unreadable, not unsafe -- {sx.get('sheet_routing')}")
changes, unreadable = v.pr_sheet_changes(v.load_classifier(sr), sr, board_sha, ["static/styles.css"])
check(changes == {"static/styles.css": {".board-card"}} and unreadable == [],
      f"pr_sheet_changes: merge commit vs its first parent -- {changes} / {unreadable}")
(sr / "scripts" / "classify_e2e.py").write_text(
    SHEET_CLASSIFIER.replace("def changed_selectors", "def _no_changed_selectors")
                    .replace("def classify(paths, config, sheet_changes=None):",
                             "def classify(paths, config, *, sheet_changes=None):"),
    encoding="utf-8")
sx_old = v.routing_report(sr, pr_list=sheet_prs, proposed_path=sr / "proposed.toml")
check(sx_old["status"] == "ok" and sx_old["sheet_routing"] == "n/a: classifier routes file lists only"
      and sx_old["counterfactual"]["changed"] == [],
      f"a classifier without changed_selectors keeps file-list routing -- {sx_old.get('sheet_routing')}")
check(rr["sheet_routing"] == "n/a: no shared_stylesheets declared", "no declared sheet -> sheet routing n/a")
