"""History pass for the `/context-purge` skill: find, relocate and verify history.

Decision and measurement history ("measured on the 2026-09-12 merge round…",
"Roberto's decision of …", issue-number war stories) belongs in the issue it
came from, not in the markdown every agent reads. Deleting it loses the record;
leaving it teaches each reader that every change adds a paragraph. This helper
moves it, and proves the move happened:

  scan      list history-shaped sentences in markdown files: a sentence with
            an ISO date, or one citing an issue alongside a narrative cue.
            Candidates only; the purge run judges which are history.
  relocate  post one span verbatim as a marked comment on an issue, read the
            comment back, and only then replace the span in the file with a
            pointer. Idempotent: an issue already carrying the text is not
            posted to again.
  verify    for every markdown file the branch changed, each removed sentence
            that carries a date, or an issue reference no longer in the
            changed files, must be found verbatim in a marked comment on an
            issue it or its replacement cites. Unverified history fails.

stdlib only. CLI (from the repo root):
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/context-purge/history.py scan <file> [<file> ...] [--json]
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/context-purge/history.py relocate <file> --issue <repo#N|#N> --span <text> [--pointer <text>]
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/context-purge/history.py verify --base origin/main
Exit 0 = pass; 2 = history removed with no verified destination (or a
relocate that could not be confirmed); 3 = git or gh could not answer, so
the outcome is unknown, never a pass.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
import git_run  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402

MARKER = "context-purge-history"
GH_TIMEOUT_SECONDS = 60

_DATE = re.compile(r"(?<!\d)(?:19|20)\d\d-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])(?!\d)")
_REF = re.compile(
    r"(?:(?<![\w/.-])(?P<repo>[A-Za-z][\w.-]*(?:/[\w.-]+)?)|(?<![\w&/#]))#(?P<num>\d{1,5})(?![\w-])"
)
_CUE = re.compile(
    r"\b(?:was|were|had|found|caught|used to|previously|silently|incident|re-?measured|"
    r"measured|regress\w*|broke|took|stranded|observed|decision of|learned|happened)\b",
    re.I,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z*`(\[_\"'])")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_QUOTE = re.compile(r"^>\s?(.*)$")


def norm(text: str) -> str:
    """Whitespace-collapsed text, the form every comparison here uses."""
    return " ".join(text.split())


def prose_lines(text: str) -> List[Tuple[int, str]]:
    """(1-based line number, line) for every line outside a fenced code block."""
    out: List[Tuple[int, str]] = []
    fence: Optional[str] = None
    for no, line in enumerate(text.splitlines(), 1):
        m = _FENCE.match(line)
        if m:
            mark = m.group(1)[0]
            fence = None if fence == mark else (fence or mark)
            continue
        if fence is None:
            out.append((no, line))
    return out


def sentences(line: str) -> List[str]:
    return [s for s in (norm(p) for p in _SENTENCE_END.split(line)) if s]


def refs(text: str, default_repo: str) -> Set[str]:
    """Canonical `owner/name#N` keys for every issue reference in `text`."""
    owner = default_repo.split("/", 1)[0]
    found: Set[str] = set()
    for m in _REF.finditer(text):
        repo = m.group("repo")
        if repo is None:
            repo = default_repo
        elif "/" not in repo:
            repo = f"{owner}/{repo}"
        found.add(f"{repo}#{int(m.group('num'))}")
    return found


def has_history(text: str, default_repo: str, surviving_refs: Set[str]) -> bool:
    """A date, or an issue reference whose pointer did not survive the rewrite."""
    return bool(_DATE.search(text)) or bool(refs(text, default_repo) - surviving_refs)


# ---- scan --------------------------------------------------------------------


@dataclass
class Candidate:
    path: str
    line: int
    kind: str  # dated | narrative
    refs: List[str]
    sentence: str


def scan_text(path: str, text: str, default_repo: str) -> List[Candidate]:
    out: List[Candidate] = []
    for no, line in prose_lines(text):
        for s in sentences(line):
            cited = sorted(refs(s, default_repo))
            if _DATE.search(s):
                out.append(Candidate(path, no, "dated", cited, s))
            elif cited and _CUE.search(s):
                out.append(Candidate(path, no, "narrative", cited, s))
    return out


# ---- relocation comments -----------------------------------------------------


def comment_body(path: str, span: str) -> str:
    quoted = "\n".join(f"> {ln}" if ln else ">" for ln in span.splitlines())
    return (
        f"<!-- {MARKER} file={path} -->\n"
        f"History moved out of `{path}` by the `/context-purge` history pass; "
        f"the file keeps a pointer to this issue.\n\n{quoted}\n"
    )


def relocated_quotes(bodies: Iterable[str]) -> List[str]:
    """The normalised quoted text of every marked relocation comment."""
    out: List[str] = []
    for body in bodies:
        if f"<!-- {MARKER} " not in body:
            continue
        quoted = [m.group(1) for m in (_QUOTE.match(ln) for ln in body.splitlines()) if m]
        if quoted:
            out.append(norm(" ".join(quoted)))
    return out


def is_relocated(text: str, quotes: Iterable[str]) -> bool:
    t = norm(text)
    return any(t in q for q in quotes)


# ---- verify core (pure; gh is injected) --------------------------------------


@dataclass
class Removed:
    path: str
    line: int
    sentence: str
    candidates: Set[str] = field(default_factory=set)


def removed_history(
    changes: Dict[str, Tuple[str, Optional[str]]], default_repo: str,
) -> List[Removed]:
    """History-bearing sentences the branch removed.

    `changes` maps path -> (before, after); after is None for a deleted file.
    A sentence counts as kept when it appears anywhere in the changed files'
    after-text, so text moved between files is not history lost. Candidate
    issues are the sentence's own references plus those on the after-lines
    that replaced it.
    """
    kept: Set[str] = set()
    after_refs: Set[str] = set()
    for _, after in changes.values():
        if after is None:
            continue
        after_refs |= refs(after, default_repo)
        for _, line in prose_lines(after):
            kept.update(sentences(line))

    out: List[Removed] = []
    for path, (before, after) in changes.items():
        b_lines = before.splitlines()
        a_lines = (after or "").splitlines()
        prose_nos = {no for no, _ in prose_lines(before)}
        matcher = difflib.SequenceMatcher(a=b_lines, b=a_lines, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            near = refs("\n".join(a_lines[max(j1 - 1, 0):j2 + 1]), default_repo)
            for idx in range(i1, i2):
                if idx + 1 not in prose_nos:
                    continue
                for s in sentences(b_lines[idx]):
                    if s in kept or not has_history(s, default_repo, after_refs):
                        continue
                    out.append(Removed(path, idx + 1, s, refs(s, default_repo) | near))
    return out


def verify_removed(
    removed: List[Removed], default_repo: str, after_refs: Set[str],
    fetch_quotes: Callable[[str], List[str]],
) -> Tuple[List[Removed], List[Removed]]:
    """(verified, missing). A removed sentence passes when, once every relocated
    quote on its candidate issues is cut out of it, what is left carries no
    history: relocating a clause and keeping the rest of the sentence is fine."""
    verified: List[Removed] = []
    missing: List[Removed] = []
    for r in removed:
        quotes = [q for issue in sorted(r.candidates) for q in fetch_quotes(issue)]
        # A span relocated from mid-sentence (a list item without its bullet, a
        # clause) is cut sentence by sentence, not only as one whole quote.
        fragments = sorted({f for q in quotes for f in (q, *sentences(q)) if f}, key=len, reverse=True)
        residual = r.sentence
        if any(residual in q for q in quotes):
            residual = ""
        for f in fragments:
            if f in residual:
                residual = residual.replace(f, " ")
        (missing if residual and has_history(residual, default_repo, after_refs) else verified).append(r)
    return verified, missing


# ---- gh / git plumbing -------------------------------------------------------


class Unknown(RuntimeError):
    """git or gh could not answer, so there is no verdict."""


def default_repo(repo: Path) -> str:
    slug = git_run.origin_slug(repo)
    if not slug:
        raise Unknown(f"cannot read the GitHub origin of {repo}")
    return slug


def split_issue(key: str) -> Tuple[str, int]:
    repo, _, num = key.rpartition("#")
    return repo, int(num)


def fetch_comment_bodies(key: str) -> List[str]:
    repo, num = split_issue(key)
    res = git_run.run_gh(
        ["issue", "view", str(num), "--repo", repo, "--json", "comments"],
        timeout=GH_TIMEOUT_SECONDS,
    )
    if res.returncode != 0:
        raise Unknown(f"gh issue view {key} failed: {res.stderr.strip()[:200]}")
    return [c.get("body", "") for c in json.loads(res.stdout).get("comments", [])]


def post_comment(key: str, body: str) -> None:
    repo, num = split_issue(key)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(body)
        body_path = fh.name
    try:
        res = git_run.run_gh(
            ["issue", "comment", str(num), "--repo", repo, "--body-file", body_path],
            timeout=GH_TIMEOUT_SECONDS,
        )
    finally:
        Path(body_path).unlink(missing_ok=True)
    if res.returncode != 0:
        raise Unknown(f"gh issue comment {key} failed: {res.stderr.strip()[:200]}")


def is_markdown_target(path: str) -> bool:
    """Instruction files plus README and docs/ markdown: what agents read."""
    name = path.rsplit("/", 1)[-1]
    if name in ("SKILL.md", "AGENTS.md", "README.md") or name.endswith("CLAUDE.md"):
        return True
    return path.endswith(".md") and (path.startswith("docs/") or "/.claude/rules/" in f"/{path}")


def branch_changes(repo: Path, base: str) -> Dict[str, Tuple[str, Optional[str]]]:
    mb = git_run.run_git(["-C", str(repo), "merge-base", base, "HEAD"])
    if mb.returncode != 0 or not mb.stdout.strip():
        raise Unknown(f"no merge-base with {base}")
    sha = mb.stdout.strip()
    diff = git_run.run_git(["-C", str(repo), "diff", "--name-status", "--no-renames", sha, "--"])
    if diff.returncode != 0:
        raise Unknown(f"git diff against {sha[:12]} failed")
    changes: Dict[str, Tuple[str, Optional[str]]] = {}
    for line in diff.stdout.splitlines():
        status, _, path = line.partition("\t")
        if not path or not is_markdown_target(path):
            continue
        before = ""
        if not status.startswith("A"):
            shown = git_run.run_git_bytes(["-C", str(repo), "show", f"{sha}:{path}"])
            if shown.returncode != 0:
                raise Unknown(f"merge-base bytes of {path} unreadable")
            before = shown.stdout.decode("utf-8", errors="replace")
        after = None if status.startswith("D") else (repo / path).read_text(encoding="utf-8")
        changes[path] = (before, after)
    return changes


# ---- commands ----------------------------------------------------------------


def cmd_scan(paths: List[Path], as_json: bool) -> int:
    repo_name = default_repo(Path("."))
    found = [c for p in paths for c in scan_text(p.as_posix(), p.read_text(encoding="utf-8"), repo_name)]
    if as_json:
        print(json.dumps([c.__dict__ for c in found], indent=1))
    else:
        for c in found:
            print(f"CANDIDATE {c.path}:{c.line} kind={c.kind} refs={','.join(c.refs) or '-'} :: {c.sentence}")
    print(f"HISTORY_SCAN files={len(paths)} candidates={len(found)}")
    return 0


def cmd_relocate(path: Path, issue: str, span: str, pointer: str) -> int:
    repo_name = default_repo(Path("."))
    keys = refs(issue if "#" in issue else f"#{issue}", repo_name)
    if len(keys) != 1:
        print(f"FAIL  --issue {issue!r} is not one issue reference")
        return 2
    key = keys.pop()
    raw = path.read_bytes().decode("utf-8")
    if raw.count(span) != 1:
        print(f"FAIL  span occurs {raw.count(span)} times in {path.as_posix()}; it must occur exactly once")
        return 2
    if not is_relocated(span, relocated_quotes(fetch_comment_bodies(key))):
        post_comment(key, comment_body(path.as_posix(), span))
    if not is_relocated(span, relocated_quotes(fetch_comment_bodies(key))):
        print(f"FAIL  comment on {key} not found after posting; {path.as_posix()} left unchanged")
        return 2
    path.write_bytes(raw.replace(span, pointer).encode("utf-8"))
    print(f"RELOCATED {path.as_posix()} -> {key}")
    return 0


def cmd_verify(repo: Path, base: str) -> int:
    repo_name = default_repo(repo)
    changes = branch_changes(repo, base)
    after_refs: Set[str] = set()
    for _, after in changes.values():
        after_refs |= refs(after or "", repo_name)
    cache: Dict[str, List[str]] = {}

    def fetch(key: str) -> List[str]:
        if key not in cache:
            cache[key] = relocated_quotes(fetch_comment_bodies(key))
        return cache[key]

    removed = removed_history(changes, repo_name)
    verified, missing = verify_removed(removed, repo_name, after_refs, fetch)
    for r in verified:
        print(f"RELOCATED_OK {r.path}:{r.line} :: {r.sentence[:100]}")
    for r in missing:
        cands = ",".join(sorted(r.candidates)) or "none"
        print(f"MISSING {r.path}:{r.line} (issues checked: {cands}) :: {r.sentence}")
    verdict = "fail" if missing else "pass"
    print(f"HISTORY_VERIFY={verdict} files={len(changes)} removed={len(removed)} "
          f"relocated={len(verified)} missing={len(missing)} base={base}")
    return 2 if missing else 0


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Find, relocate and verify history in markdown.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("files", type=Path, nargs="+")
    p_scan.add_argument("--json", action="store_true")
    p_rel = sub.add_parser("relocate")
    p_rel.add_argument("file", type=Path)
    p_rel.add_argument("--issue", required=True, help="repo#N, owner/repo#N or #N (this repo)")
    p_rel.add_argument("--span", required=True, help="exact text to move; must occur once")
    p_rel.add_argument("--pointer", default="", help="text left in its place")
    p_ver = sub.add_parser("verify")
    p_ver.add_argument("--base", required=True)
    p_ver.add_argument("--repo", type=Path, default=Path("."))
    args = ap.parse_args(argv)
    try:
        if args.cmd == "scan":
            return cmd_scan(args.files, args.json)
        if args.cmd == "relocate":
            return cmd_relocate(args.file, args.issue, args.span, args.pointer)
        return cmd_verify(args.repo, args.base)
    except (Unknown, OSError, json.JSONDecodeError) as exc:
        print(f"UNKNOWN  {exc}")
        if args.cmd == "verify":
            print(f"HISTORY_VERIFY=unknown base={args.base}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
