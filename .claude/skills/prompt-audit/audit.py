"""Deterministic half of `/prompt-audit` (fleet-config#831, #832).

The skill audits every fleet instruction file against the vendors' current
prompting guidance. "The helper measures, the orchestrator judges": this module
does every exact, reproducible step — hashing fetched guides against their
baselines, enumerating the scan surface, counting lint hits, collapsing findings
shared with the scaffolding master, the skip-unchanged ledger, cadence state, and
rendering the digest — so none of those numbers is ever invented by a model. It
never fetches anything (the orchestrator's own tool does) and never edits an
instruction file.

Vendor and audience vocabulary is data (`sources.toml` `[audiences.*]`); the
rule-set is `rules.md`, whose tags decide whether a hit is a `violation` or only
`consider` for a given file audience.

Subcommands (every line-oriented output uses the fleet `KEY=value|...` protocol):

  sources                         SOURCE=<id>|url=...|baseline=<sha>|marker=...
  diff-source --id I --file F [--final-url U]
                                  VERDICT=unchanged|changed|new-guide|not-checked|...
  inventory [--only REPO]         FILE=<path>|audience=...|kind=...|sha=...|lines=N
                                  SECTION=<path>#L<a>-L<b>|audience=...|heading=...
  lint (--file PATH | --all) [--only REPO] [--changed-only] [--rescan-all] [--detail]
                                  HITS=<path>|...|hits=R-01:2,R-13:4|... (+ HIT= lines)
  dedup --findings JSON           annotated findings JSON + DEDUP= summary
  state show | state mark (--source ID --verdict V | --scan) [--date D]
  ledger plan [--only REPO] [--rescan-all]
  ledger write --recorded FILE [--date D] [--dry-run]
  ledger comment --body-file FILE
  digest --run JSON               the run digest markdown

State lives in `~/.claude/prompt-audit/state.json` (override the directory with
`PROMPT_AUDIT_STATE_DIR`); a corrupt file degrades to everything due. The ledger
is the audit-managed `kind=prompt-audit` issue on fleet-config.

stdlib + `skills/_lib` only. Run with the repo venv from the fleet-config root:
    E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/prompt-audit/audit.py inventory
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skills" / "_lib"))
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
NEUTRAL = "neutral"
VERDICTS = ("unchanged", "changed", "new-guide", "not-checked")
FINDING_VERDICTS = ("violation", "consider", "compliant", "unmeasured")

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


# ---- freshness gate -------------------------------------------------------------

_DEFAULT_MODEL_LIST = r"including ((?:[^.]|\.\d)+)\."


def extract_marker(kind: str, text: str, cfg: dict) -> Optional[str]:
    """The source's version marker, "" for kind `none`, None when not extractable."""
    if kind == "none":
        return ""
    if kind == "model-list":
        m = re.search(cfg.get("marker_pattern") or _DEFAULT_MODEL_LIST, text)
        return clean(m.group(1)) if m else None
    if kind == "llms-txt-lines":
        pattern = cfg.get("marker_pattern")
        if not pattern:
            return None
        found = sorted(set(re.findall(pattern, text)))
        return ",".join(found) if found else None
    if kind == "latest-model-frontmatter":
        if not text.startswith("---"):
            return None
        end = text.find("\n---", 3)
        block = text[3:end] if end != -1 else ""
        key = re.escape(cfg.get("marker_key", "model"))
        m = re.search(rf"^\s*{key}:\s*(\S+)\s*$", block, re.MULTILINE)
        return m.group(1) if m else None
    raise ValueError(f"unknown marker_kind {kind!r}")


def diff_source(cfg: dict, data: Optional[bytes], final_url: Optional[str] = None) -> dict:
    """Verdict for one fetched source against its baseline.

    `data` None / empty -> `not-checked`: an unfetched page is never `unchanged`.
    An `index` source is judged on its marker alone (its sha moves with every new
    docs page); a gained marker line is `new-guide`, a lost one `changed`. A
    `latest-model-frontmatter` marker change is `new-guide`. Otherwise the sha
    decides. A moved redirect target is `changed` in its own right.
    """
    kind = cfg.get("marker_kind", "none")
    if not data:
        return {"verdict": "not-checked", "sha": "unmeasured", "marker": "unmeasured",
                "reason": "fetched file missing or empty"}
    text = data.decode("utf-8", errors="replace")
    sha = sha12(data)
    marker = extract_marker(kind, text, cfg)
    shown = "unmeasured" if marker is None else (marker or "none")
    out = {"sha": sha, "marker": shown}
    baseline_sha = cfg.get("baseline_sha", "")
    baseline_marker = cfg.get("baseline_marker", "")
    expected_url = cfg.get("baseline_final_url")
    if final_url and expected_url and final_url != expected_url:
        return {**out, "verdict": "changed", "reason": f"redirect target moved to {final_url}"}
    if kind == "llms-txt-lines":
        if marker is None:
            return {**out, "verdict": "changed", "reason": "no guide lines found in the index"}
        now, before = set(marker.split(",")), set(filter(None, baseline_marker.split(",")))
        if now - before:
            return {**out, "verdict": "new-guide", "reason": "new guide lines: " + ",".join(sorted(now - before))}
        if before - now:
            return {**out, "verdict": "changed", "reason": "guide lines removed: " + ",".join(sorted(before - now))}
        moved = "index sha moved, guide lines identical" if sha != baseline_sha else "identical"
        return {**out, "verdict": "unchanged", "reason": moved}
    if kind == "latest-model-frontmatter" and marker != baseline_marker:
        return {**out, "verdict": "new-guide", "reason": f"flagship marker {baseline_marker} -> {shown}"}
    if sha != baseline_sha:
        reason = f"sha {baseline_sha} -> {sha}"
        if kind == "model-list" and marker != baseline_marker:
            reason += "; model list changed"
        return {**out, "verdict": "changed", "reason": reason}
    return {**out, "verdict": "unchanged", "reason": "identical"}


# ---- audiences + inventory ------------------------------------------------------

@dataclass
class Entry:
    key: str          # fleet-root-relative posix path, e.g. fleet-config/global-CLAUDE.md
    path: Path
    repo: str
    kind: str
    audience: str = NEUTRAL
    data: Optional[bytes] = None

    @property
    def sha(self) -> str:
        return sha12(self.data) if self.data is not None else "unmeasured"

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace") if self.data is not None else ""


def kind_of(relpath: str) -> Optional[str]:
    name = relpath.rsplit("/", 1)[-1]
    if name == "SKILL.md":
        return "skill"
    if "/.claude/rules/" in f"/{relpath}" and name.endswith(".md"):
        return "rules"
    if name.endswith("CLAUDE.md"):
        return "claude-md"
    if name == "AGENTS.md":
        return "agents-md"
    return None


def agents_pointer(repo_dir: Path, target: str) -> bool:
    agents = repo_dir / "AGENTS.md"
    try:
        return agents.is_file() and target in agents.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def audience_of(entry: Entry, repo_dir: Path, audiences: Dict[str, dict]) -> str:
    for name, cfg in audiences.items():
        if any(fnmatch.fnmatch(entry.key, g) for g in cfg.get("path_globs", [])):
            return name
    base = entry.key.rsplit("/", 1)[-1]
    for name, cfg in audiences.items():
        if base in cfg.get("unpointed", []) and not agents_pointer(repo_dir, base):
            return name
    return NEUTRAL


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def inventory(repos: Dict[str, Path], audiences: Dict[str, dict],
              only: Optional[str] = None) -> List[Entry]:
    """The scan surface in audit order, worktrees excluded, one entry per path.

    (1) the home repo's global file, (2) the scaffolding master, (3) every repo's
    CLAUDE.md / AGENTS.md / .claude/rules/*.md, (4) every SKILL.md in the home
    repo's two skill tiers and each repo's .claude/skills/.
    """
    live = {name: d for name, d in sorted(repos.items())
            if d.is_dir() and not is_linked_worktree(d)}
    wanted: List[Tuple[str, Path]] = []
    home = live.get(HOME_REPO)
    if home:
        wanted.append((HOME_REPO, home / "global-CLAUDE.md"))
    if MASTER_REPO in live:
        wanted.append((MASTER_REPO, live[MASTER_REPO] / "CLAUDE.md"))
    for name, d in live.items():
        wanted += [(name, d / "CLAUDE.md"), (name, d / "AGENTS.md")]
        wanted += [(name, p) for p in sorted((d / ".claude" / "rules").glob("*.md"))]
    if home:
        for tier in ("skills", ".claude/skills"):
            wanted += [(HOME_REPO, p) for p in sorted((home / tier).glob("*/SKILL.md"))]
    for name, d in live.items():
        if name != HOME_REPO:
            wanted += [(name, p) for p in sorted((d / ".claude" / "skills").glob("*/SKILL.md"))]

    seen, out = set(), []
    for name, path in wanted:
        if only and name != only:
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(live[name]).as_posix()
        key = f"{name}/{rel}"
        kind = kind_of(rel)
        if key in seen or kind is None:
            continue
        seen.add(key)
        entry = Entry(key=key, path=path, repo=name, kind=kind, data=_read(path))
        entry.audience = audience_of(entry, live[name], audiences)
        out.append(entry)
    return out


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def sections(text: str, file_audience: str, audiences: Dict[str, dict]) -> List[dict]:
    """Heading sections whose audience differs from the file's (marker-scoped)."""
    lines = text.splitlines()
    heads = []
    in_fence = False
    for i, line in enumerate(lines, start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else _HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    out = []
    for idx, (start, level, heading) in enumerate(heads):
        aud = next((n for n, c in audiences.items()
                    if any(mk in heading for mk in c.get("markers", []))), None)
        if not aud or aud == file_audience:
            continue
        end = len(lines)
        for nxt, nlevel, _ in heads[idx + 1:]:
            if nlevel <= level:
                end = nxt - 1
                break
        out.append({"start": start, "end": end, "audience": aud, "heading": heading})
    return out


def audience_at(line_no: int, file_audience: str, secs: List[dict]) -> str:
    inner = [s for s in secs if s["start"] <= line_no <= s["end"]]
    return max(inner, key=lambda s: s["start"])["audience"] if inner else file_audience


# ---- rules ----------------------------------------------------------------------

_RULE_HEAD = re.compile(r"^### (R-\d{2}) (.+?)\s+tags:\s*(.+)$")


def parse_rules(text: str) -> Dict[str, dict]:
    rules: Dict[str, dict] = {}
    current = None
    for line in text.splitlines():
        m = _RULE_HEAD.match(line)
        if m:
            tags = re.findall(r"\[([^\]]+)\]", m.group(3))
            plain = [t for t in tags if ":" not in t]
            kv = dict(t.split(":", 1) for t in tags if ":" in t)
            current = {"id": m.group(1), "title": m.group(2).strip(),
                       "vendor": plain[0].strip() if plain else "",
                       "file": kv.get("file", "any").strip(), "tier": kv.get("tier", "").strip(),
                       "detect": ""}
            rules[current["id"]] = current
        elif current and line.startswith("Detect:"):
            current["detect"] = line.split(":", 1)[1].strip().split(" ", 1)[0]
    return rules


def rule_applies_to_kind(rule: dict, kind: str) -> bool:
    scope = rule.get("file", "any")
    if scope == "any":
        return True
    if scope == "claude-md":
        return kind in ALWAYS_ON_KINDS
    return scope == kind


def verdict_cap(vendor_tag: str, audience: str, audiences: Dict[str, dict]) -> Optional[str]:
    """Strongest verdict a rule can reach for a reader; None = does not apply."""
    if vendor_tag == "shared":
        return "violation"
    if vendor_tag == "conflict":
        return "violation" if audience == NEUTRAL else None
    if audience == NEUTRAL:
        return "consider"
    return "violation" if audiences.get(audience, {}).get("vendor") == vendor_tag else None


# ---- lint -----------------------------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"

PATTERNS: Dict[str, List[re.Pattern]] = {
    "R-01": [re.compile(r"\b(?:MUST|NEVER|ALWAYS|CRITICAL|IMPORTANT|REQUIRED|MANDATORY|NOT|DON'T)\b")],
    "R-02": [re.compile(r"\b(?:if|when) in doubt\b|\bdefault to (?:using|calling|running|invoking)\b", re.I)],
    "R-03": [re.compile(r"\bdouble[- ]check|\bre-?verify\b|\bverify your (?:answer|work|output|response|changes)\b"
                        r"|\bfinal verification step\b|\bsubagent to verify\b|\bre-?check your\b", re.I)],
    "R-04": [re.compile(r"\bevery\s+(?:\d+|few|couple of)\s+tool[- ]calls?\b"
                        r"|\bafter every\s+\d+\s+(?:tool[- ]calls?|steps)\b", re.I)],
    "R-05": [re.compile(r"\bhold (?:all |every |any )?(?:findings|results|updates|output)\s+"
                        r"(?:for|until)\s+(?:the\s+)?(?:final|end)\b", re.I)],
    "R-07": [re.compile(r"\bthink step[- ]by[- ]step\b|\bstep[- ]by[- ]step (?:reasoning|thinking)\b"
                        r"|\breason step[- ]by[- ]step\b", re.I),
             re.compile(r"^\s*\d+[.)]\s+(?:think|reason|reflect)\b", re.I)],
    "R-08": [re.compile(r"\b(?:do not|don't|never)\s+(?:think|reason)\b(?!\s+(?:about|of|that)\b)", re.I)],
    "R-09": [re.compile(r"\bonly report (?:high|critical)[- ]severity\b|\bbe conservative\b"
                        r"|\b(?:do not|don't) nitpick\b", re.I)],
    "R-10": [re.compile(r"\bprefill(?:ed|s|ing)?\b|\bbudget_tokens\b", re.I)],
    "R-11": [re.compile(rf"\b(?:before|after|until|since|as of|by)\s+(?:{_MONTHS})\s+\d{{4}}\b", re.I)],
    "R-12": [re.compile(r"\b(?:outline|present|share|state|write)\s+(?:a|an|your)\s+(?:upfront\s+)?plan\s+before\b"
                        r"|\bupfront plan\b|\bbegin (?:each|every) (?:response|turn) with a plan\b", re.I)],
}
# Line-level detectors: one hit per matching line, not per occurrence.
LINE_PATTERNS: Dict[str, re.Pattern] = {
    "R-06": re.compile(r"\b(?:do not|don't|never|avoid|no)\s+(?:use\s+|using\s+)?"
                       r"(?:markdown|bullet(?:s| points)?|headers|headings|bold|lists|formatting)\b", re.I),
    "R-13": re.compile(r"\b(?:never|do not|don't|avoid|must not|mustn't)\b", re.I),
}
_MODAL = re.compile(r"\b(always|must not|must|never|do not|don't)\s+([a-z][a-z-]*(?:\s+[a-z][a-z-]*){0,2})", re.I)
# "I" only as a capitalised standalone word (never the I in "I/O"); the rest any case.
_PERSON = re.compile(r"\bI\b(?!/)|\b(?i:me|my|we|our|you|your)\b")
LINT_RULES = sorted(set(PATTERNS) | set(LINE_PATTERNS) | {"R-14", "R-15", "R-16", "R-17"})


@dataclass
class Hit:
    rule: str
    line: int
    count: int
    text: str
    cap: str = "violation"


@dataclass
class LintResult:
    entry: Entry
    lines: int
    hits: List[Hit] = field(default_factory=list)
    neg: Tuple[int, int] = (0, 0)
    desc_words: str = "n/a"
    desc_chars: str = "n/a"
    size: str = ""

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for h in self.hits:
            out[h.rule] = out.get(h.rule, 0) + h.count
        return dict(sorted(out.items()))


def body_start(text: str) -> int:
    """1-based line number where a frontmatter-bearing file's body begins."""
    if not text.startswith("---"):
        return 1
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            return i + 2
    return 1


def instruction_lines(text: str) -> List[Tuple[int, str]]:
    """(line number, text) for lintable prose: no frontmatter, fences or inline code."""
    out = []
    in_fence = False
    start = body_start(text)
    for i, raw in enumerate(text.splitlines(), start=1):
        if i < start:
            continue
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out.append((i, _INLINE_CODE.sub(" ", raw)))
    return out


def paragraphs(lines: List[Tuple[int, str]]) -> List[List[Tuple[int, str]]]:
    paras, cur = [], []
    for n, line in lines:
        if line.strip():
            cur.append((n, line))
        elif cur:
            paras.append(cur)
            cur = []
    if cur:
        paras.append(cur)
    return paras


def _excerpt(line: str) -> str:
    return clean(line)[:120]


def lint_entry(entry: Entry, rules: Dict[str, dict], audiences: Dict[str, dict]) -> LintResult:
    text = entry.text
    raw_lines = text.splitlines()
    res = LintResult(entry=entry, lines=len(raw_lines))
    secs = sections(text, entry.audience, audiences)
    lines = instruction_lines(text)

    def add(rule_id: str, line_no: int, count: int, excerpt: str) -> None:
        rule = rules.get(rule_id)
        if rule is None or not rule_applies_to_kind(rule, entry.kind):
            return
        cap = verdict_cap(rule["vendor"], audience_at(line_no, entry.audience, secs), audiences)
        if cap is not None:
            res.hits.append(Hit(rule_id, line_no, count, _excerpt(excerpt), cap))

    instr = negative = 0
    for n, line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Occurrence patterns read headings too ("## CRITICAL"); line patterns and the
        # negative ratio count instruction lines only, so hits and ratio agree.
        for rule_id, pats in PATTERNS.items():
            count = sum(len(p.findall(line)) for p in pats)
            if count:
                add(rule_id, n, count, raw_lines[n - 1])
        if _HEADING.match(stripped):
            continue
        instr += 1
        negative += bool(LINE_PATTERNS["R-13"].search(line))
        for rule_id, pat in LINE_PATTERNS.items():
            if pat.search(line):
                add(rule_id, n, 1, raw_lines[n - 1])
    # The ratio is a measurement of the file, reported whether or not R-13 applies to its reader.
    res.neg = (negative, instr)

    # R-14 size cap
    unit, limit = SIZE_CAPS[entry.kind]
    if unit == "lines":
        size = len(raw_lines)
    elif unit == "body-lines":
        size = len(raw_lines) - body_start(text) + 1
    else:
        size = len(entry.data or b"")
    res.size = f"{size}{'b' if unit == 'bytes' else 'l'}/{limit}"
    if size > limit:
        add("R-14", 1, 1, f"{unit} {size} over cap {limit}")

    # R-15 skill description
    if entry.kind == "skill":
        err = frontmatter_error(text)
        desc = "" if err else frontmatter_description(text)
        if desc:
            prose = strip_quoted(desc)
            res.desc_words = str(prose_words(desc))
            res.desc_chars = str(len(desc))
            if len(desc) > 1024:
                add("R-15", 1, 1, f"description {len(desc)} chars over 1024")
            person = len(_PERSON.findall(prose))
            if person:
                add("R-15", 1, person, f"description: {prose}")
        else:
            res.desc_words = res.desc_chars = "unmeasured"

    # R-16 mechanically visible contradictions
    polarity: Dict[str, Dict[str, int]] = {}
    for n, line in lines:
        for modal, phrase in _MODAL.findall(line):
            sign = "+" if modal.lower() in ("always", "must") else "-"
            polarity.setdefault(phrase.lower(), {}).setdefault(sign, n)
    for phrase, signs in sorted(polarity.items()):
        if len(signs) == 2:
            first = min(signs.values())
            add("R-16", first, 1, raw_lines[first - 1])

    # R-17 vendor paragraph without a marker (only where the reader is neutral)
    all_markers = [mk for c in audiences.values() for mk in c.get("markers", [])]
    for para in paragraphs(lines):
        first = para[0][0]
        if audience_at(first, entry.audience, secs) != NEUTRAL:
            continue
        joined = " ".join(l for _, l in para)
        if any(mk in joined for mk in all_markers):
            continue
        named = [a for a, c in audiences.items()
                 if any(re.search(rf"\b{re.escape(t)}\b", joined) for t in c.get("terms", []))]
        if len(named) == 1:
            add("R-17", first, 1, raw_lines[first - 1])
    res.hits.sort(key=lambda h: (h.line, h.rule))
    return res


def hits_line(res: LintResult) -> str:
    counts = res.counts()
    hits = ",".join(f"{k}:{v}" for k, v in counts.items()) or "none"
    return (f"HITS={res.entry.key}|audience={res.entry.audience}|kind={res.entry.kind}"
            f"|lines={res.lines}|size={res.size}|hits={hits}|neg={res.neg[0]}/{res.neg[1]}"
            f"|desc_words={res.desc_words}|desc_chars={res.desc_chars}")


def hit_detail(res: LintResult) -> List[str]:
    return [f"HIT={res.entry.key}:{h.line}|rule={h.rule}|cap={h.cap}|count={h.count}|text={h.text}"
            for h in res.hits]


# ---- scaffold dedup -------------------------------------------------------------

_MARKUP = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+|[*_`>#]")


def norm_line(s: str) -> str:
    return re.sub(r"\s+", " ", _MARKUP.sub("", s)).strip().lower()


def dedup(findings: List[dict], master_text: str, lite_text: str,
          master_key: str = f"{MASTER_REPO}/CLAUDE.md",
          global_key: str = f"{HOME_REPO}/global-CLAUDE.md") -> List[dict]:
    """Annotate each finding with where it should be filed.

    `shared-with-scaffold`: the offending line also appears (normalised) in the
    scaffolding master, so it is filed once there with a `propagate_to` list of
    every repo carrying it. `shared-with-lite`: a global-file line that also sits
    in the lite port's global instructions. Otherwise `repo-local`. Lines under
    20 normalised characters never match — too generic to call shared.
    """
    master = {norm_line(l) for l in master_text.splitlines() if len(norm_line(l)) >= 20}
    lite = {norm_line(l) for l in lite_text.splitlines() if len(norm_line(l)) >= 20}
    groups: Dict[Tuple[str, str], set] = {}
    out = []
    for f in findings:
        n = norm_line(f.get("text", ""))
        repo = f["path"].split("/", 1)[0]
        g = dict(f)
        if f["path"] != master_key and len(n) >= 20 and n in master:
            g.update(scope="shared-with-scaffold", file_against=master_key)
            groups.setdefault((f["rule"], n), set()).add(repo)
        elif f["path"] == global_key and len(n) >= 20 and n in lite:
            g.update(scope="shared-with-lite", file_against=global_key, propagate_to=[LITE_REPO])
        else:
            g.update(scope="scaffold-master" if f["path"] == master_key else "repo-local",
                     file_against=f["path"])
        out.append(g)
    for g in out:
        n = norm_line(g.get("text", ""))
        if g["scope"] in ("shared-with-scaffold", "scaffold-master"):
            g["propagate_to"] = sorted(groups.get((g["rule"], n), set()))
    return out


# ---- state ----------------------------------------------------------------------

def state_path() -> Path:
    override = os.environ.get("PROMPT_AUDIT_STATE_DIR")
    base = Path(override) if override else Path.home() / ".claude" / "prompt-audit"
    return base / "state.json"


def load_state(path: Optional[Path] = None) -> dict:
    p = path or state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # missing or corrupt -> everything due; costs one extra fetch pass
    return data if isinstance(data, dict) and isinstance(data.get("sources", {}), dict) else {}


def save_state(state: dict, path: Optional[Path] = None) -> None:
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def source_due(record: Optional[dict], every: int, today: dt.date) -> Tuple[bool, str]:
    """A source is fresh only if checked within the cadence AND last seen unchanged."""
    if not record or not record.get("last"):
        return True, "now"
    try:
        last = dt.date.fromisoformat(record["last"])
    except (TypeError, ValueError):
        return True, "now"
    nxt = last + dt.timedelta(days=every)
    if record.get("verdict") != "unchanged" or today >= nxt:
        return True, "now"
    return False, nxt.isoformat()


# ---- ledger ---------------------------------------------------------------------

_ENTRY_RE = re.compile(r"^([^:#\s<][^:]*?):[ \t]*([0-9a-f]{12})[ \t]*$", re.MULTILINE)


def parse_ledger(body: str) -> dict:
    idx = (body or "").find(BLOCK_MARKER)
    if idx == -1:
        return {"rubric": None, "run_at": None, "files": {}}
    block = body[idx:]
    end = block.find("-->", len(BLOCK_MARKER))
    block = block[:end] if end != -1 else block
    rub = re.search(r"^rubric-sha:[ \t]*([0-9a-f]+)[ \t]*$", block, re.MULTILINE)
    at = re.search(r"^last-run-at:[ \t]*(\S+)[ \t]*$", block, re.MULTILINE)
    return {"rubric": rub.group(1) if rub else None, "run_at": at.group(1) if at else None,
            "files": {m.group(1).strip(): m.group(2) for m in _ENTRY_RE.finditer(block)}}


def plan_scan(current: Dict[str, str], ledger: dict, rubric: str,
              rescan_all: bool = False) -> Dict[str, Tuple[str, str]]:
    plan = {}
    for key, sha in sorted(current.items()):
        if sha == "unmeasured":
            plan[key] = ("unmeasured", "unreadable")
        elif rescan_all:
            plan[key] = ("scan", "rescan-all")
        elif not ledger.get("rubric"):
            plan[key] = ("scan", "no-ledger")
        elif ledger["rubric"] != rubric:
            plan[key] = ("scan", "rubric-changed")
        elif key not in ledger["files"]:
            plan[key] = ("scan", "new")
        elif ledger["files"][key] != sha:
            plan[key] = ("scan", "changed")
        else:
            plan[key] = ("skip", "unchanged")
    return plan


def merge_ledger(ledger: dict, recorded: Dict[str, str], rubric: str) -> Tuple[Dict[str, str], int]:
    """New entries; prior entries survive only under the same rubric. Returns (files, dropped)."""
    base = dict(ledger["files"]) if ledger.get("rubric") == rubric else {}
    base.update(recorded)
    keys = sorted(base)
    return {k: base[k] for k in keys[:LEDGER_CAP]}, max(0, len(keys) - LEDGER_CAP)


def render_ledger_body(files: Dict[str, str], rubric: str, run_at: str,
                       sources: Dict[str, dict], dropped: int = 0) -> str:
    rows = [f"| `{sid}` | {c.get('vendor', '')} | {c.get('role', '')} | `{c.get('baseline_sha', '')}` "
            f"| {clean(str(c.get('baseline_marker') or '—'))} | {c.get('baseline_date', '')} |"
            for sid, c in sources.items()]
    lines = [
        "Standing ledger for `/prompt-audit` (fleet-config#831) — vendor-guide baselines plus the skip-unchanged file table. "
        "Managed by `.claude/skills/prompt-audit/audit.py`; do not hand-edit the block. The last comment is the current state of the audit.",
        "",
        "## Guide baselines (`sources.toml`)",
        "",
        "| source | vendor | role | baseline sha | marker | date |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        f"## Assessed files ({len(files)}{f', {dropped} over the cap — rescanned next run' if dropped else ''})",
        "",
        BLOCK_MARKER,
        "<!--",
        f"last-run-at: {run_at}",
        f"rubric-sha: {rubric}",
        *[f"{k}: {v}" for k, v in sorted(files.items())],
        "-->",
        "",
        f"rubric-sha `{rubric[:12]}` · last run {run_at} · {len(files)} file hashes recorded (hidden block above).",
    ]
    return "\n".join(lines) + "\n"


def _audit_issue(*args: str) -> str:
    return run_audit_issue(AUDIT_ISSUE, *args)


def read_ledger_issue() -> dict:
    data = json.loads(_audit_issue("get", "--repo", LEDGER_REPO, "--kind", KIND))
    return {"number": data.get("number"), **parse_ledger(data.get("body") or "")}


# ---- digest ---------------------------------------------------------------------

def _kv(line: str) -> dict:
    head, _, rest = line.partition("|")
    key, _, value = head.partition("=")
    out = {key: value}
    for part in rest.split("|"):
        k, sep, v = part.partition("=")
        if sep:
            out[k] = v
    return out


def render_digest(run: dict, rules: Dict[str, dict], master_text: str = "", lite_text: str = "") -> Tuple[str, str]:
    """(markdown, status). Pure: every count comes from `run`, nothing is inferred.

    `run`: {date, dry_run, sources: [VERDICT= lines], update_issue, scan_ran,
    plan: [PLAN= lines], judgments: {path: [finding, ...] | null}, rubric}.
    A planned-scan file with no judgment (absent or null) is `unmeasured`, never
    compliant; skipped files are listed as skipped.
    """
    srcs = [_kv(l) for l in run.get("sources", [])]
    by_verdict = {v: [s for s in srcs if s.get("VERDICT") == v] for v in VERDICTS}
    stale = by_verdict["changed"] + by_verdict["new-guide"]
    guides = "changed" if stale else ("not-checked" if by_verdict["not-checked"] or not srcs else "unchanged")
    plan = [_kv(l) for l in run.get("plan", [])]
    scan = [p["PLAN"] for p in plan if p.get("action") == "scan"]
    skip = [p["PLAN"] for p in plan if p.get("action") == "skip"]
    unreadable = [p["PLAN"] for p in plan if p.get("action") == "unmeasured"]
    judgments = run.get("judgments") or {}
    scan_ran = bool(run.get("scan_ran"))
    unmeasured = (unreadable + [k for k in scan if judgments.get(k) is None]) if scan_ran else []
    judged = [k for k in scan if judgments.get(k) is not None] if scan_ran else []
    findings = [dict(f, path=k) for k in judged for f in judgments[k]
                if f.get("verdict") in ("violation", "consider")]
    status = "partial" if unmeasured else "complete"

    out = [f"## prompt-audit digest — {run.get('date', '')}", "",
           f"`status={status}` · `guides={guides}` · `rubric={str(run.get('rubric', ''))[:12]}`"
           + (" · **dry run — nothing written**" if run.get("dry_run") else ""), ""]
    out.append(f"**Guides:** {len(srcs)} sources — " + ", ".join(
        f"{v} {len(by_verdict[v])}" for v in VERDICTS))
    for s in stale + by_verdict["not-checked"]:
        out.append(f"- `{s.get('id')}` **{s.get('VERDICT')}** — {s.get('reason', '')}")
    out.append("")
    if not scan_ran:
        ref = run.get("update_issue") or "(update issue not filed)"
        out += [f"**Scan:** not run — rule-set stale, see {ref}", ""]
        return "\n".join(out) + "\n", status

    out.append(f"**Scan:** {len(plan)} files — scanned {len(judged)}, skipped {len(skip)} (unchanged), "
               f"unmeasured {len(unmeasured)}")
    tally: Dict[str, Dict[str, int]] = {}
    for f in findings:
        tally.setdefault(f["rule"], {"violation": 0, "consider": 0})[f["verdict"]] += 1
    out += ["", f"**Findings:** {sum(t['violation'] for t in tally.values())} violation, "
                f"{sum(t['consider'] for t in tally.values())} consider, across "
                f"{len({f['path'] for f in findings})} files", ""]
    if tally:
        out += ["| rule | violation | consider |", "|---|---|---|"]
        out += [f"| {r} {rules.get(r, {}).get('title', '')} | {t['violation']} | {t['consider']} |"
                for r, t in sorted(tally.items())]
        out.append("")

    annotated = dedup(findings, master_text, lite_text)
    shared: Dict[Tuple[str, str], dict] = {}
    local = []
    for f in annotated:
        if f["scope"] == "shared-with-scaffold":
            shared.setdefault((f["rule"], norm_line(f.get("text", ""))), f)
        else:
            local.append(f)
    if shared:
        out += ["### Shared with the scaffolding master (file once there)", ""]
        for f in shared.values():
            out.append(f"- **{f['rule']}** {f['verdict']} — `{clean(f.get('text', ''))[:100]}` "
                       f"— propagate to: {', '.join(f.get('propagate_to', []))}")
        out.append("")
    if local:
        out += ["### Findings", ""]
        for f in local[:DIGEST_FINDINGS_CAP]:
            where = f"{f['path']}:{f['line']}" if f.get("line") else f["path"]
            extra = f" — propagate to: {', '.join(f['propagate_to'])}" if f.get("propagate_to") else ""
            out.append(f"- `{where}` **{f['rule']}** {f['verdict']} — {clean(f.get('note') or f.get('text', ''))[:160]}{extra}")
        if len(local) > DIGEST_FINDINGS_CAP:
            out.append(f"- … {len(local) - DIGEST_FINDINGS_CAP} more not listed (cap {DIGEST_FINDINGS_CAP})")
        out.append("")
    if unmeasured:
        out += ["### Unmeasured (not established — not compliant)", ""]
        out += [f"- `{k}`" for k in unmeasured] + [""]
    if skip:
        out += ["<details><summary>Skipped — unchanged since the last scan under this rubric "
                f"({len(skip)})</summary>", ""]
        out += [f"- `{k}`" for k in skip] + ["", "</details>", ""]
    body = "\n".join(out) + "\n"
    if len(body) > COMMENT_CHAR_CAP:
        body = body[:COMMENT_CHAR_CAP] + "\n\n… digest truncated at the comment size cap.\n"
    return body, status


# ---- CLI ------------------------------------------------------------------------

def _repos(args: argparse.Namespace) -> Dict[str, Path]:
    return fleet_repos(Path(args.projects_toml)) if getattr(args, "projects_toml", None) else fleet_repos()


def _master_and_lite(repos: Dict[str, Path]) -> Tuple[str, str]:
    root = REPO_ROOT.parent
    master = repos.get(MASTER_REPO, root / MASTER_REPO) / "CLAUDE.md"
    lite = repos.get(LITE_REPO, root / LITE_REPO) / LITE_GLOBAL
    return ((_read(master) or b"").decode("utf-8", "replace"),
            (_read(lite) or b"").decode("utf-8", "replace"))


def cmd_sources(cfg: dict) -> int:
    for sid, s in cfg.get("sources", {}).items():
        print(f"SOURCE={sid}|url={s['url']}|vendor={s.get('vendor', '')}|role={s.get('role', '')}"
              f"|marker_kind={s.get('marker_kind', 'none')}|baseline={s.get('baseline_sha', '')}"
              f"|marker={clean(str(s.get('baseline_marker') or 'none'))}")
    print(f"SOURCES={len(cfg.get('sources', {}))}")
    return 0


def cmd_diff_source(cfg: dict, sid: str, file: str, final_url: Optional[str]) -> int:
    src = cfg.get("sources", {}).get(sid)
    if src is None:
        print(f"❌ unknown source id {sid!r} — not in {SOURCES_TOML.name}", file=sys.stderr)
        return 2
    p = Path(file)
    data = _read(p) if p.is_file() else None
    v = diff_source(src, data, final_url)
    print(f"VERDICT={v['verdict']}|id={sid}|sha={v['sha']}|marker={clean(v['marker'])}|reason={clean(v['reason'])}")
    return 0


def _entries(args: argparse.Namespace, cfg: dict) -> List[Entry]:
    return inventory(_repos(args), cfg.get("audiences", {}), getattr(args, "only", None))


def cmd_inventory(args: argparse.Namespace, cfg: dict) -> int:
    entries = _entries(args, cfg)
    audiences = cfg.get("audiences", {})
    for e in entries:
        lines = str(len(e.text.splitlines())) if e.data is not None else "unmeasured"
        print(f"FILE={e.key}|audience={e.audience}|kind={e.kind}|sha={e.sha}|lines={lines}")
        for s in sections(e.text, e.audience, audiences):
            print(f"SECTION={e.key}#L{s['start']}-L{s['end']}|audience={s['audience']}|heading={clean(s['heading'])}")
    print(f"INVENTORY={len(entries)}|repos={len({e.repo for e in entries})}")
    return 0


def cmd_lint(args: argparse.Namespace, cfg: dict) -> int:
    rules = parse_rules(RULES_MD.read_text(encoding="utf-8"))
    audiences = cfg.get("audiences", {})
    if args.file:
        path = Path(args.file).resolve()
        entry = Entry(key=path.as_posix(), path=path, repo="", kind=kind_of(path.name) or "claude-md",
                      data=_read(path))
        if "/.claude/rules/" in path.as_posix():
            entry.kind = "rules"
        match = next((e for e in _entries(args, cfg) if e.path.resolve() == path), None)
        if match is not None:
            entry = match
        entries = [entry]
    else:
        entries = _entries(args, cfg)
        if args.changed_only:
            ledger = read_ledger_issue()
            plan = plan_scan({e.key: e.sha for e in entries}, ledger,
                             rules_rubric(), args.rescan_all)
            entries = [e for e in entries if plan[e.key][0] == "scan"]
    total = 0
    for e in entries:
        if e.data is None:
            print(f"HITS={e.key}|audience={e.audience}|kind={e.kind}|lines=unmeasured|hits=unmeasured")
            continue
        res = lint_entry(e, rules, audiences)
        if args.detail or args.file:
            for line in hit_detail(res):
                print(line)
        print(hits_line(res))
        total += sum(res.counts().values())
    print(f"LINT={len(entries)} files|hits={total}")
    return 0


def cmd_dedup(args: argparse.Namespace) -> int:
    findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    repos = _repos(args)
    master, lite = _master_and_lite(repos)
    out = dedup(findings, master, lite)
    print(json.dumps(out, indent=2))
    counts = {s: sum(1 for f in out if f["scope"] == s)
              for s in ("shared-with-scaffold", "shared-with-lite", "scaffold-master", "repo-local")}
    print("DEDUP=" + "|".join(f"{k}={v}" for k, v in counts.items()), file=sys.stderr)
    return 0


def cmd_state(args: argparse.Namespace, cfg: dict) -> int:
    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    state = load_state()
    if args.action == "show":
        every = int(cfg.get("check_every_days", 7))
        due = 0
        for sid in cfg.get("sources", {}):
            rec = state.get("sources", {}).get(sid)
            is_due, nxt = source_due(rec, every, today)
            due += is_due
            print(f"SOURCE_STATE={sid}|due={'yes' if is_due else 'no'}|last={(rec or {}).get('last', 'never')}"
                  f"|last_verdict={(rec or {}).get('verdict', 'none')}|next_due={nxt}")
            if not is_due:
                # Fresh means "last seen unchanged, within cadence": the digest line says so, cached.
                print(f"VERDICT=unchanged|id={sid}|sha=cached|marker=cached"
                      f"|reason=checked {rec['last']}, fresh until {nxt}")
        print(f"SCAN_STATE=last={state.get('last_scan', 'never')}")
        print(f"DUE={due}")
        return 0
    if args.scan:
        state["last_scan"] = today.isoformat()
        save_state(state)
        print(f"✅ marked scan {today.isoformat()}")
        return 0
    if not args.source or args.verdict not in VERDICTS or args.verdict == "not-checked":
        print("❌ state mark needs --scan, or --source ID with --verdict unchanged|changed|new-guide "
              "(a not-checked source was not checked and is never marked)", file=sys.stderr)
        return 2
    if args.source not in cfg.get("sources", {}):
        print(f"❌ unknown source id {args.source!r}", file=sys.stderr)
        return 2
    state.setdefault("sources", {})[args.source] = {"last": today.isoformat(), "verdict": args.verdict}
    save_state(state)
    print(f"✅ marked {args.source} {args.verdict} {today.isoformat()}")
    return 0


def cmd_ledger(args: argparse.Namespace, cfg: dict) -> int:
    rubric = rules_rubric()
    if args.action == "plan":
        entries = _entries(args, cfg)
        ledger = read_ledger_issue()
        plan = plan_scan({e.key: e.sha for e in entries}, ledger, rubric, args.rescan_all)
        for e in entries:
            action, reason = plan[e.key]
            print(f"PLAN={e.key}|action={action}|reason={reason}|sha={e.sha}")
        n = {a: sum(1 for v in plan.values() if v[0] == a) for a in ("scan", "skip", "unmeasured")}
        print(f"LEDGER=#{ledger['number'] or 'none'}|rubric={rubric[:12]}|ledger_rubric={(ledger['rubric'] or 'none')[:12]}"
              f"|scan={n['scan']}|skip={n['skip']}|unmeasured={n['unmeasured']}")
        return 0
    if args.action == "write":
        by_key = {e.key: e.sha for e in inventory(_repos(args), cfg.get("audiences", {}))}
        recorded: Dict[str, str] = {}
        for raw in Path(args.recorded).read_text(encoding="utf-8").splitlines():
            parts = raw.split()
            if not parts:
                continue
            sha = parts[1] if len(parts) > 1 else by_key.get(parts[0])
            if parts[0] not in by_key or not sha or sha == "unmeasured":
                print(f"❌ not a measured file in the scan surface: {parts[0]}", file=sys.stderr)
                return 2
            recorded[parts[0]] = sha
        ledger = read_ledger_issue()
        files, dropped = merge_ledger(ledger, recorded, rubric)
        body = render_ledger_body(files, rubric, args.date or dt.date.today().isoformat(),
                                  cfg.get("sources", {}), dropped)
        if args.dry_run:
            print(body)
            print(f"LEDGER_WRITE=dry-run|recorded={len(recorded)}|entries={len(files)}")
            return 0
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(body)
            tmp = fh.name
        try:
            url = _audit_issue("upsert", "--repo", LEDGER_REPO, "--kind", KIND, "--label", "audit-meta",
                               "--title", TITLE, "--body-file", tmp).strip()
        finally:
            Path(tmp).unlink(missing_ok=True)
        print(f"LEDGER_WRITE={url}|recorded={len(recorded)}|entries={len(files)}|dropped={dropped}")
        return 0
    # comment
    number = read_ledger_issue()["number"]
    if number is None:
        print("❌ no prompt-audit ledger issue yet — run `ledger write` first", file=sys.stderr)
        return 2
    res = git_run.run_gh(["issue", "comment", str(number), "--repo", LEDGER_REPO,
                          "--body-file", args.body_file], timeout=120)
    if res.returncode != 0:
        print(f"❌ gh issue comment failed: {(res.stderr or res.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"LEDGER_COMMENT={(res.stdout or '').strip()}")
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    run = json.loads(Path(args.run).read_text(encoding="utf-8"))
    rules = parse_rules(RULES_MD.read_text(encoding="utf-8"))
    run.setdefault("rubric", rules_rubric())
    master, lite = _master_and_lite(_repos(args))
    body, status = render_digest(run, rules, master, lite)
    print(body)
    print(f"DIGEST=status={status}", file=sys.stderr)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ensure_utf8_stdio()
    ap = argparse.ArgumentParser(description="Deterministic half of /prompt-audit.")
    ap.add_argument("--projects-toml", default=None, help="fleet membership file (tests)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources")
    d = sub.add_parser("diff-source")
    d.add_argument("--id", required=True)
    d.add_argument("--file", required=True)
    d.add_argument("--final-url", default=None)
    inv = sub.add_parser("inventory")
    inv.add_argument("--only", default=None)
    li = sub.add_parser("lint")
    target = li.add_mutually_exclusive_group(required=True)
    target.add_argument("--file")
    target.add_argument("--all", action="store_true")
    li.add_argument("--only", default=None)
    li.add_argument("--changed-only", action="store_true", help="only files the ledger plan would scan")
    li.add_argument("--rescan-all", action="store_true")
    li.add_argument("--detail", action="store_true", help="print HIT= lines for --all too")
    dd = sub.add_parser("dedup")
    dd.add_argument("--findings", required=True)
    st = sub.add_parser("state")
    st.add_argument("action", choices=("show", "mark"))
    st.add_argument("--source")
    st.add_argument("--verdict")
    st.add_argument("--scan", action="store_true")
    st.add_argument("--date", default=None)
    lg = sub.add_parser("ledger")
    lg.add_argument("action", choices=("plan", "write", "comment"))
    lg.add_argument("--only", default=None)
    lg.add_argument("--rescan-all", action="store_true")
    lg.add_argument("--recorded")
    lg.add_argument("--date", default=None)
    lg.add_argument("--dry-run", action="store_true")
    lg.add_argument("--body-file")
    dg = sub.add_parser("digest")
    dg.add_argument("--run", required=True)
    args = ap.parse_args(argv)

    cfg = load_toml()
    if args.cmd == "sources":
        return cmd_sources(cfg)
    if args.cmd == "diff-source":
        return cmd_diff_source(cfg, args.id, args.file, args.final_url)
    if args.cmd == "inventory":
        return cmd_inventory(args, cfg)
    if args.cmd == "lint":
        return cmd_lint(args, cfg)
    if args.cmd == "dedup":
        return cmd_dedup(args)
    if args.cmd == "state":
        return cmd_state(args, cfg)
    if args.cmd == "ledger":
        if args.action == "write" and not args.recorded:
            ap.error("ledger write needs --recorded")
        if args.action == "comment" and not args.body_file:
            ap.error("ledger comment needs --body-file")
        return cmd_ledger(args, cfg)
    return cmd_digest(args)


if __name__ == "__main__":
    raise SystemExit(main())
