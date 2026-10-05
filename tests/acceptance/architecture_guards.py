"""Architecture / fleet-map freshness guards (fleet-config#502).

Split out of the former tests/run_acceptance.py god-module: concern (b) --
`/system-map` and `/config-map`'s coverage, `.fleet.toml` aggregation,
Mermaid companion render, week-over-week whatchanged diffs, and the live
`~/.claude/settings.json` <-> template sync check. Each returns
`(failures, total)` except `_settings_template_sync_check`, which returns a
third `skipped` count (no live settings.json to compare against, or a
template hook whose module is not live on `main` yet).
"""
from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Tuple

from acceptance.shared import HOOKS, PYTHON, REPO, _Checker


# A card row is a markdown table row whose FIRST cell names the project, in
# bold, optionally after an icon: `| 🚀 **app-launcher** | ... |`. Anchoring to
# the first cell is the whole point — a bare `r in doc` substring test passed
# for `mcp-personal-onedrive` purely because the External-integrations table
# mentions it in prose in a *second* cell, so the guard could not detect the
# one thing it exists to detect: a mapped repo with no card of its own
# (fleet-config#681). Non-repo rows (`**Telegram**`, `**GPU**`) also match and are
# harmless: the set is only ever tested for membership of known repo names.
_CARD_ROW_RE = re.compile(r"^\|[^|]*?\*\*([A-Za-z0-9][A-Za-z0-9._-]*)\*\*[^|]*\|", re.MULTILINE)


def _architecture_card_repos(doc: str) -> set:
    """Project names carrying their own card row in ARCHITECTURE.md."""
    return set(_CARD_ROW_RE.findall(doc))


# A tree-entry line names exactly one path token right after its box-drawing
# prefix (`├── `, `└── `, or a `│   ` continuation for a nested entry) --
# everything past the run of whitespace that follows is a trailing comment,
# not the entry itself. A whole-block `"codex/" in layout` substring test also
# matches `~/.codex/hooks` and `~/.codex/prompts` named in *other* entries'
# comment prose, so a directory with no tree entry of its own anywhere in the
# block can still read as present (fleet-config#820, same shape as
# `_CARD_ROW_RE` above).
_TREE_ENTRY_RE = re.compile(r"^[\s│├└─]*(\S+)", re.MULTILINE)


def _layout_tree_entries(layout: str) -> set:
    """Every literal entry token a Markdown tree block's lines introduce."""
    return set(_TREE_ENTRY_RE.findall(layout))


def _layout_top_dirs(layout: str) -> set:
    """Top-level directory names an entry token actually introduces.

    This README's tree sometimes spells a top-level directory alone
    (`hooks/`) and sometimes fuses it with a deeper path in one line
    (`agy/plugins/fleet-context-filter/`, `pi/extensions/statusline.ts`,
    `tests/run_acceptance.py`) rather than giving it its own nested lines --
    so "the entry token equals `<dir>/`" is too strict and would misreport
    those as missing. Taking each token's first path segment covers both
    spellings while still requiring a real tree entry, not a comment mention.
    """
    return {token.split("/", 1)[0] for token in _layout_tree_entries(layout) if "/" in token}


def _system_map_coverage_check() -> Tuple[int, int]:
    """The system map must cover exactly the fleet, and the doc must agree.

    Guards the `/system-map` single source of truth (architecture/fleet.data.js)
    against drift, mechanically:
      1. every fleet repo (projects.toml − [global] architecture_ignore) appears
         on the map;
      2. no map entry is a stale/typo'd repo absent from the fleet;
      3. every mapped repo also appears in ARCHITECTURE.md (data ↔ doc agree).
    Returns the failure count.
    """
    import json
    import tomllib

    check = _Checker()

    arch = REPO / "architecture"
    toml = tomllib.loads((REPO / "hooks" / "projects.toml").read_text(encoding="utf-8"))
    ignore = set(toml.get("global", {}).get("architecture_ignore", []))
    fleet = {
        name for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
    } - ignore

    # fleet.data.js holds `window.FLEET = { ...strict JSON... };` — slice the object out.
    raw = (arch / "fleet.data.js").read_text(encoding="utf-8")
    data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    mapped = {
        e.get("repo", e["nm"])
        for section in ("governance", "enabling", "web", "pipe")
        for e in data.get(section, [])
    }

    missing = fleet - mapped
    stale = mapped - fleet
    check(f"system_map: every fleet repo is on the map (missing: {sorted(missing) or 'none'})", not missing)
    check(f"system_map: no stale map entries (stale: {sorted(stale) or 'none'})", not stale)

    doc = (arch / "ARCHITECTURE.md").read_text(encoding="utf-8")
    carded = _architecture_card_repos(doc)
    doc_missing = sorted(r for r in mapped if r not in carded)
    check(f"system_map: every mapped repo has a card row in ARCHITECTURE.md (missing: {doc_missing or 'none'})", not doc_missing)

    # The matcher itself, against the shape that defeated the old substring
    # test: `mcp-personal-onedrive` named in prose in a *second* cell of the
    # External-integrations table, with no card row of its own anywhere.
    _prose_only = (
        "| Service | Reached from | For |\n"
        "|---|---|---|\n"
        "| 📁 **OneDrive** | apps: email-archiver, ghost-repo | file browsing |\n"
    )
    check("system_map: the card matcher ignores a repo named only in another cell's prose",
          "ghost-repo" not in _architecture_card_repos(_prose_only))
    check("system_map: the card matcher accepts a real icon+bold first-cell card row",
          "ghost-repo" in _architecture_card_repos("| 📁 **ghost-repo** | what it is | pipeline | — |\n"))
    check("system_map: the card matcher accepts a bold first cell with no icon",
          "ghost-repo" in _architecture_card_repos("| **ghost-repo** | what it is |\n"))

    return check.failures, check.total


_REGEN_HINT = (
    "regenerate + commit with `/system-map`, or directly:\n"
    "E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/system-map/build_data.py"
)


def _fleet_toml_check() -> Tuple[int, int, int]:
    """Per-repo `.fleet.toml` aggregation is fresh and can't silently go stale.

    Guards the self-describing map (`build_data.py`: residual + per-repo
    `.fleet.toml` → `fleet.data.js`). Split by *whose commit can fix a failure*
    (fleet-config#562):

    **Hard** — inputs this repo owns, so a fresh clone on any machine gets the
    same answer:
      1. fleet-config's own card in the committed `fleet.data.js` matches
         fleet-config's own committed `.fleet.toml` — the anti-staleness
         contract for the one card this repo can actually keep current.

    **Advisory** (reported, counted as *skipped*, never failed) — inputs that
    live in sibling checkouts, so no commit here can make them green:
      2. `fleet.data.js` is exactly what `build_data.py` regenerates;
      3. every repo in the residual's `_adopted` registry still carries a
         `.fleet.toml` on its committed default branch;
      4. every present `.fleet.toml` is a valid declaration.

    2-4 used to be hard, which meant a `.fleet.toml` commit in *any* sister repo
    turned this repo's gate red — blocking every `/issue-finish`, `/quick`, and
    `/issue-yolo` here, for a reason the author of the change could not see,
    until the weekly `/system-map` run regenerated the aggregate (observed on
    `main` at c70b88f: home-automation added a Modbus chip and this gate went
    red for two days). `/system-map` owns fleet-wide freshness — it regenerates
    and commits weekly, and `build_data.py --check` fails loud there.

    Returns (failures, total, skipped).
    """
    import importlib.util
    import tomllib

    check = _Checker()

    bd_path = REPO / ".claude" / "skills" / "system-map" / "build_data.py"
    spec = importlib.util.spec_from_file_location("system_map_build_data", bd_path)
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)

    committed_text = (REPO / "architecture" / "fleet.data.js").read_text(encoding="utf-8")

    # --- hard: fleet-config's own card, both sides committed in this repo ---
    own_detail = ""
    try:
        own_toml = bd.read_fleet_toml(REPO)
        if own_toml is None:
            own_ok, own_detail = False, "fleet-config has no committed .fleet.toml"
        else:
            section, own_card = bd.card_from_toml("fleet-config", tomllib.loads(own_toml))
            committed = json.loads(
                committed_text[committed_text.index("{"): committed_text.rindex("}") + 1]
            )
            mapped = [e for e in committed.get(section, []) if e.get("repo", e.get("nm")) == "fleet-config"]
            own_ok = mapped == [own_card]
            if not own_ok:
                own_detail = f"declared: {own_card}\nmapped:   {mapped}\n{_REGEN_HINT}"
    except Exception as exc:  # noqa: BLE001 - a malformed own declaration is our bug
        own_ok, own_detail = False, str(exc)
    check("fleet_toml: fleet-config's own card matches its own .fleet.toml", own_ok, own_detail)

    # --- advisory: everything below reads sibling repos' live checkouts ---
    try:
        fresh, regen_err = bd.regenerate() == committed_text, ""
    except Exception as exc:  # noqa: BLE001 - surface a malformed declaration cleanly
        fresh, regen_err = False, f" ({exc})"
    check.advisory(
        f"fleet_toml: fleet.data.js matches build_data.py output{regen_err}",
        fresh,
        f"a sibling repo's .fleet.toml moved ahead of the committed aggregate.\n{_REGEN_HINT}",
    )

    residual = bd.load_residual()
    repos = bd.fleet_repos()
    adopted = residual.get("_adopted", [])
    missing = [r for r in adopted if r not in repos or bd.read_fleet_toml(repos[r]) is None]
    check.advisory(
        f"fleet_toml: every adopted repo still has a .fleet.toml (missing: {sorted(missing) or 'none'})",
        not missing,
        "fix in the owning repo (or drop it from architecture/fleet.residual.json `_adopted`).",
    )

    invalid = []
    for name, repo_dir in sorted(repos.items()):
        text = bd.read_fleet_toml(repo_dir)
        if text is None:
            continue
        try:
            bd.card_from_toml(name, tomllib.loads(text))
        except Exception as exc:  # noqa: BLE001
            invalid.append(f"{name}: {exc}")
    check.advisory(
        f"fleet_toml: every present .fleet.toml is valid (invalid: {invalid or 'none'})",
        not invalid,
        "fix the declaration in the owning repo; schema: architecture/README.md.",
    )

    return check.failures, check.total, check.skipped


def _description_cap_check() -> Tuple[int, int]:
    """No map card's description runs past what its card shows in two lines (#1250).

    Hard, unlike `_fleet_toml_check`'s fleet-wide half: both inputs live in
    this repo. The committed `fleet.data.js` changes only when it is
    regenerated here, and `build_data.py` refuses an over-cap `.fleet.toml`
    (keeping the residual fallback card), so a sibling repo's commit can never
    turn this red. The residual's fallback cards are this repo's own text.
    Also pins the refusal itself against fixtures. Returns the failure count.
    """
    import importlib.util

    check = _Checker()

    bd_path = REPO / ".claude" / "skills" / "system-map" / "build_data.py"
    spec = importlib.util.spec_from_file_location("system_map_build_data_cap", bd_path)
    bd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bd)
    layer_of = {section: layer for layer, section in bd.LAYER_SECTION.items()}

    def over_cap(data: dict) -> list:
        return [
            f"{e.get('repo', e['nm'])} ({len(e['ds'])} > {bd.DESCRIPTION_CAP[layer_of[s]]})"
            for s in layer_of
            for e in data.get(s, [])
            if len(e["ds"]) > bd.DESCRIPTION_CAP[layer_of[s]]
        ]

    raw = (REPO / "architecture" / "fleet.data.js").read_text(encoding="utf-8")
    mapped = over_cap(json.loads(raw[raw.index("{"): raw.rindex("}") + 1]))
    check(f"description_cap: every mapped card fits two lines (over: {mapped or 'none'})", not mapped,
          f"shorten the owning repo's .fleet.toml description.\n{_REGEN_HINT}")
    fallback = over_cap(bd.load_residual())
    check(f"description_cap: every residual fallback card fits two lines (over: {fallback or 'none'})",
          not fallback, "shorten the card in architecture/fleet.residual.json.")

    cap = bd.DESCRIPTION_CAP["working-pipe"]
    meta = {"layer": "working-pipe", "icon": "📄", "description": "x" * cap}
    check("description_cap: a description at the cap is accepted",
          bd.card_from_toml("ghost-repo", meta)[1]["ds"] == "x" * cap)
    try:
        bd.card_from_toml("ghost-repo", {**meta, "description": "x" * (cap + 1)})
        refused_by_name = False
    except bd.DescriptionTooLong as exc:
        refused_by_name = "ghost-repo" in str(exc)
    check("description_cap: card_from_toml refuses one char over the cap, by repo name", refused_by_name)

    long_toml = f'layer = "working-pipe"\nicon = "📄"\ndescription = "{"x" * (cap + 1)}"\n'
    residual = {"pipe": [{"ic": "📄", "nm": "ghost-repo", "ds": "short fallback"}]}
    real_read = bd.read_fleet_toml
    bd.read_fleet_toml = lambda _repo_dir: long_toml
    try:
        refused: list = []
        built = bd.build(residual, {"ghost-repo": REPO}, refused)
        check("description_cap: build keeps the fallback card for a refused repo and names it",
              built["pipe"] == residual["pipe"] and len(refused) == 1 and "ghost-repo" in refused[0])
        try:
            bd.build({"pipe": []}, {"ghost-repo": REPO}, [])
            raised = False
        except bd.DescriptionTooLong as exc:
            raised = "ghost-repo" in str(exc)
        check("description_cap: build raises for a refused repo with no fallback card", raised)
    finally:
        bd.read_fleet_toml = real_read

    return check.failures, check.total


def _fleet_membership_drift_check() -> Tuple[int, int, int]:
    """The fleet on disk and the fleet in `projects.toml` are the same set (#640).

    `CLAUDE.md` makes that block the fleet-membership list — `fleet_repos()`
    reads it, so an omission silently narrows `/system-map`, `/config-map`,
    `/context-audit`'s cap gate and `chief_ops.py verify`, and leaves
    `notify_on_idle` pinging `[claude]` instead of naming the project. Nothing
    caught that: `local-llm-hub-lite` was worked by six `/cleanup-fleet-all`
    lanes while being invisible to every fleet report, because the list is
    maintained by hand and drift is silent by construction.

    So: every real fleet repo sitting next to this one must be declared, or
    named in `[global] architecture_ignore` — the documented "deliberately off
    the map" escape hatch. Hard, not `advisory`: unlike `_fleet_toml_check`'s
    fleet-wide half, the fix is a one-line commit *in this repo*, which is
    exactly the kind of failure a gate is for.

    Membership comes from the shared `fleet_repo_scan.iter_fleet_repos` rather
    than a fresh crawl, so the linked-worktree guard is inherited instead of
    re-derived — a sibling `<repo>-wt-<N>` is a full checkout and counting one
    as a repo is the same mistake #629 already fixed once.

    Finding no repos at all is its own state, not a pass: on a fresh clone or
    another machine there is no fleet next door to compare against, and a run
    that verified nothing must never read like one that verified everything
    (fleet-config#461, #501). Returns (failures, total, skipped).
    """
    import importlib.util
    import tomllib

    spec = importlib.util.spec_from_file_location(
        "fleet_repo_scan", REPO / "skills" / "_lib" / "fleet_repo_scan.py"
    )
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)  # type: ignore[union-attr]

    root = REPO.parent  # E:/automation — the fleet lives beside this checkout
    on_disk = {d.name for d in scan.iter_fleet_repos(root)}
    if not on_disk:
        print(f"SKIP  fleet_membership: no fleet repos found beside this checkout in {root} (skipped)")
        return 0, 0, 1

    toml = tomllib.loads((REPO / "hooks" / "projects.toml").read_text(encoding="utf-8"))
    declared = {
        name for name, tbl in toml.items()
        if name != "global" and isinstance(tbl, dict) and "cwd_prefix" in tbl
    }
    ignored = set(toml.get("global", {}).get("architecture_ignore", []))

    check = _Checker()
    undeclared = sorted(on_disk - declared - ignored)
    check(
        f"fleet_membership: every repo in {root} is declared in projects.toml "
        f"(undeclared: {undeclared or 'none'})",
        not undeclared,
        "add each to hooks/projects.toml before the [global] block:\n"
        + "\n".join(f'[{r}]\ncwd_prefix = "{root.as_posix()}/{r}"' for r in undeclared)
        + "\n(then regenerate the maps per docs/adding-a-fleet-project.md), "
        "or list it in [global] architecture_ignore to keep it off the map on purpose.",
    )

    return check.failures, check.total, check.skipped


def _advisory_semantics_check() -> Tuple[int, int]:
    """`_Checker.advisory` reports, it never gates (fleet-config#562).

    The scoping decision `_fleet_toml_check` rests on: a check whose inputs live
    in sibling checkouts may turn up drift, but must not make this repo's `main`
    unshippable. Pinned mechanically, because "advisory" is one careless
    `check(...)` away from being a hard failure again — and because the opposite
    mistake (swallowing drift into the passing state) is the false "done" the
    global CLAUDE.md forbids. Returns the failure count.
    """
    check = _Checker()

    def counts(drive) -> Tuple[int, int, int]:
        """Run one probe against a throwaway _Checker, swallowing its own
        OK/FAIL/SKIP line so a deliberate failing probe can't be mistaken for a
        real one in the gate output."""
        probe = _Checker()
        with contextlib.redirect_stdout(io.StringIO()):
            drive(probe)
        return probe.failures, probe.total, probe.skipped

    check("advisory: a pass counts toward Total like any other check",
          counts(lambda c: c.advisory("probe", True)) == (0, 1, 0))
    check("advisory: a failure counts as Skipped, never Failed",
          counts(lambda c: c.advisory("probe", False, "why it drifted")) == (0, 0, 1))
    check("advisory: an ordinary check still fails hard (the escape hatch isn't global)",
          counts(lambda c: c("probe", False)) == (1, 1, 0))

    src = Path(__file__).read_text(encoding="utf-8")
    body = src.split("def _fleet_toml_check", 1)[1].split("\ndef ", 1)[0]
    check("advisory: the three fleet-wide fleet_toml checks are still advisory",
          body.count("check.advisory(") == 3 and body.count("\n    check(") == 1)

    return check.failures, check.total


def _unattended_worktree_mandate_check() -> Tuple[int, int]:
    """Every unattended dispatch path must force worktree mode (#515, #525).

    A *running* app is not a claim holder, so an ordinary `acquire` hands
    machine-dispatched work `MODE=primary` in a repo whose primary checkout is
    being served live -- that is what broke the running launcher on 2026-07-30.
    Four skills carry the rule in prose, which a context purge or a well-meaning
    rewrite can quietly drop; this pins it. Checks the flag is named in each,
    that `/issue-start` still keys on the launcher's own session variable
    rather than some re-derived heuristic, and that the skills handing off to
    `/issue-start` still reach its step-0 claim (#894). Returns the failure
    count.
    """
    check = _Checker()
    flag = "--force-worktree"

    for rel in (
        ".claude/workflows/cleanup-fleet-all.js",
        ".claude/skills/cleanup-fleet/SKILL.md",
        "skills/codebase-audit/SKILL.md",
        "skills/issue-start/SKILL.md",
    ):
        body = (REPO / rel).read_text(encoding="utf-8")
        check(f"worktree mandate: {rel} forces worktree mode", flag in body)

    issue_start = (REPO / "skills" / "issue-start" / "SKILL.md").read_text(encoding="utf-8")
    check(
        "worktree mandate: /issue-start keys the force on APP_LAUNCHER_SESSION_ID (#525)",
        "APP_LAUNCHER_SESSION_ID" in issue_start,
    )

    wc = (REPO / "skills" / "_lib" / "worktree_claim.py").read_text(encoding="utf-8")
    check(
        "worktree mandate: acquire actually implements --force-worktree",
        'print("MODE=worktree")' in wc and "force_worktree" in wc,
    )

    # A skill that *paraphrases* /issue-start instead of pointing at it drops
    # step 0 -- /issue-yolo's Phase 2 summary did, and an app-launcher lane
    # built in the live primary checkout (#894). Pin the pointer, not the prose.
    yolo = (REPO / "skills" / "issue-yolo" / "SKILL.md").read_text(encoding="utf-8")
    phase2 = yolo.partition("### Phase 2")[2].partition("### Phase 3")[0]
    check(
        "worktree mandate: /issue-yolo Phase 2 makes worktree_claim.py acquire its first step (#894)",
        "worktree_claim.py acquire" in phase2 and "APP_LAUNCHER_SESSION_ID" in phase2,
    )
    check(
        "worktree mandate: /issue-yolo Phase 2 does not restate /issue-start's git steps (#894)",
        not any(step in phase2 for step in ("git checkout main", "git pull --ff-only", "git checkout -b")),
    )
    issue_add = (REPO / "skills" / "issue-add" / "SKILL.md").read_text(encoding="utf-8")
    check(
        "worktree mandate: /issue-add's one-shot hand-off starts at /issue-start step 0 (#894)",
        "steps 1–6" not in issue_add and "steps 0–6" in issue_add,
    )

    return check.failures, check.total


# Sentence-level scan for an instruction to delete a git lock. A sentence that
# mentions a lock is flagged when a delete verb in it is not preceded, within
# a short window, by a negation ("never delete", "no delete") or by a human
# actor ("a human ... removes it") -- the two legitimate ways a skill talks
# about removing one. Heuristic on purpose: it pins the shape the drift took
# (#1243's "report it as a stale lock, delete it, retry"), not English at large.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n\s*[-*]\s+|\n\n")
_LOCK_RE = re.compile(r"index\.lock|\blocks?\b", re.IGNORECASE)
_DELETE_VERB_RE = re.compile(
    r"\b(delete[sd]?|deleting|remov(?:e|es|ed|ing)|rm|clear(?:s|ed|ing)?|unlink)\b|stale-cleared",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"\b(never|not|no|don't|nothing|neither|nor|without|cannot)\b", re.IGNORECASE)
# How far back from a delete verb a negation or "human" still governs it.
_VERB_GOVERNOR_WINDOW = 60
# `gh pr merge ... --merge` (a merge commit); `--merged` is a different flag.
_MERGE_COMMIT_RE = re.compile(r"gh pr merge[^\n`]*--merge\b(?!d)")


def _lock_delete_instructions(text: str) -> list[str]:
    """Return every sentence in `text` that tells its reader to delete a lock."""
    hits = []
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence or not _LOCK_RE.search(sentence):
            continue
        for verb in _DELETE_VERB_RE.finditer(sentence):
            before = sentence[max(0, verb.start() - _VERB_GOVERNOR_WINDOW):verb.start()]
            if not _NEGATION_RE.search(before) and "human" not in before.lower():
                hits.append(" ".join(sentence.split())[:160])
                break
    return hits


def _skill_git_rules_check() -> Tuple[int, int]:
    """No skill tells an agent to delete an index.lock or merge-commit a PR
    (fleet-config#1243).

    Global CLAUDE.md: "Report a lock, never delete one -- it is another
    process's file" (#667), and the pipeline squash-merges. Both drifted into
    skill prose that runs unattended -- `/cleanup-fleet-all` deleted stale
    locks and `/issue-finish` merged with `--merge` -- so the drift executed
    rather than merely reading wrong. Scans every skill markdown file and the
    workflow scripts; the scanner itself is pinned against fixed sentences so
    a loosened heuristic can't pass by matching nothing. Returns the failure
    count.
    """
    check = _Checker()

    check("skill git rules: the scanner flags a delete-the-lock instruction",
          bool(_lock_delete_instructions(
              "Older than 5 minutes -> report it by name and age as a stale lock, delete it, retry the pull.")))
    check("skill git rules: the scanner passes a prohibition and a human-actor clause",
          not _lock_delete_instructions(
              "**Never delete a lock from this skill**. The fix is a human confirming the "
              "holder is dead, then removing it."))
    check("skill git rules: the merge-commit pattern ignores --merged",
          bool(_MERGE_COMMIT_RE.search("`gh pr merge 12 --merge`"))
          and not _MERGE_COMMIT_RE.search("`gh pr merge 12 --squash`; git branch --merged main"))

    files = sorted(
        [p for root in ("skills", ".claude/skills") for p in (REPO / root).rglob("*.md")]
        + list((REPO / ".claude" / "workflows").glob("*.js"))
    )
    lock_hits, merge_hits = [], []
    for path in files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO).as_posix()
        lock_hits += [f"{rel}: {s}" for s in _lock_delete_instructions(text)]
        merge_hits += [f"{rel}: {m.group(0)}" for m in _MERGE_COMMIT_RE.finditer(text)]

    check(f"skill git rules: no skill instructs deleting an index.lock ({len(files)} files)",
          not lock_hits, "\n".join(lock_hits))
    check("skill git rules: every `gh pr merge` squash-merges, none uses --merge",
          not merge_hits, "\n".join(merge_hits))

    return check.failures, check.total


def _acceptance_audit_wiring_check() -> Tuple[int, int]:
    """Acceptance is audited per criterion, unverifiable as its own state
    (fleet-config#958). The rule lives in skill prose that a context purge or
    a rewrite can quietly fold back into one yes/no sentence; this pins the
    pointer to the helper and the per-criterion wording in each place. Returns
    (failures, total)."""
    check = _Checker()

    def read(rel: str) -> str:
        return (REPO / rel).read_text(encoding="utf-8")

    finish = read("skills/issue-finish/SKILL.md")
    step1 = finish.partition("### 1. Finalize the work")[2].partition("### 2.")[0]
    check("acceptance audit: /issue-finish step 1 runs acceptance_audit.py extract + tally",
          "acceptance_audit.py extract" in step1 and "tally <N>" in step1)
    check("acceptance audit: /issue-finish step 1 names all three verdicts and the unattended rule",
          all(v in step1 for v in ("`DONE`", "`NOT DONE`", "`UNVERIFIABLE`", "--unattended", "`blocked`")))
    check("acceptance audit: /issue-finish carries 'code that handles a deliverable is not the deliverable'",
          "Code that handles a" in step1 and "deliverable is not the deliverable" in step1)
    check("acceptance audit: /issue-finish step 4 puts PR_TEST_PLAN lines in the PR body",
          "PR_TEST_PLAN" in finish.partition("### 4.")[2].partition("### 5.")[0])

    yolo = read("skills/issue-yolo/SKILL.md")
    check("acceptance audit: /issue-yolo 3h verdict carries the per-criterion criteria array",
          "{criterion, proof_location, verdict, evidence}" in yolo)
    check("acceptance audit: docs/independent-review-gate.md matches the criteria array",
          "{criterion, proof_location, verdict, evidence}" in read("docs/independent-review-gate.md"))
    check("acceptance audit: /issue-add requires pass/fail-decidable criteria",
          "decidable pass/fail" in read("skills/issue-add/SKILL.md"))
    check("acceptance audit: /issue-finish-batch finishes unattended",
          "`unattended` finish" in read("skills/issue-finish-batch/SKILL.md"))
    check("acceptance audit: /cleanup-fleet-all's execute brief finishes unattended",
          "tally --unattended" in read(".claude/workflows/cleanup-fleet-all.js"))

    return check.failures, check.total


def _gate_evidence_wiring_check() -> Tuple[int, int]:
    """No finish decision rests on a remembered "green this session"
    (fleet-config#957): steps 3, 3c and 5 of /issue-finish and /e2e's dedupe
    rule ask `gate_evidence.py`, which binds a pass to the tree it ran
    against. Pins the pointer and the absence of the old phrase in exactly the
    decision paragraphs. Returns (failures, total)."""
    check = _Checker()
    finish = (REPO / "skills" / "issue-finish" / "SKILL.md").read_text(encoding="utf-8")

    def section(text: str, start: str, end: str) -> str:
        return text.partition(start)[2].partition(end)[0]

    step3 = section(finish, "### 3. Verification gate", "### 3b.")
    step3c = section(finish, "### 3c.", "### 4.")
    step5 = section(finish, "### 5.", "### 6.")
    check("gate evidence: /issue-finish step 3 runs the gate through gate_evidence.py run --label gate",
          "gate_evidence.py run --label gate" in step3)
    # "this session's to touch" (step 5's primary-checkout guard) is about
    # ownership, not a remembered green, so only the bare phrase is banned.
    remembered = re.compile(r"this session(?!'s)")
    for name, body in (("3c", step3c), ("5", step5)):
        check(f"gate evidence: /issue-finish step {name} asks gate_evidence.py check, never 'this session'",
              "check --label gate" in body and not remembered.search(body))
    check("gate evidence: the /issue-finish description no longer skips CI on 'green this session'",
          "this session" not in finish.split("\n---", 1)[0])

    e2e = (REPO / "skills" / "e2e" / "SKILL.md").read_text(encoding="utf-8")
    dedupe = section(e2e, "**Deduplicate against the verification gate", "- **Synchronous only.**")
    check("gate evidence: /e2e's dedupe rule asks check --label gate and records e2e-<tier>",
          "check --label gate" in dedupe and "run --label e2e-" in dedupe and "in this session" not in dedupe)
    batch = (REPO / "skills" / "issue-finish-batch" / "SKILL.md").read_text(encoding="utf-8")
    check("gate evidence: /issue-finish-batch's brief skips CI only on FRESH evidence",
          "gate_evidence.py check --label gate" in batch)

    return check.failures, check.total


def _chief_wait_event_check() -> Tuple[int, int]:
    """The chief wakes on worker turn-end events, not a 10-minute sleep
    (fleet-config#999). Pins the reference waiter to `wait-event` and keeps
    the old `sleep 600` poll from creeping back. Returns (failures, total)."""
    check = _Checker()
    skill = (REPO / ".claude" / "skills" / "chief" / "SKILL.md").read_text(encoding="utf-8")
    cadence = skill.partition("## Polling on a cadence")[2].partition("\n## ")[0]
    check("chief wake: the reference waiter runs chief_ops.py wait-event",
          '"$OPS" wait-event' in cadence)
    check("chief wake: the 10-minute `sleep 600` poll script is gone", "sleep 600" not in skill)
    incoming = skill.partition("## Incoming worker notifications")[2].partition("\n## ")[0]
    check("chief wake: incoming notifications point at the wait-event wake, not a timed catch-all",
          "wait-event" in incoming and "periodic Board poll is unaffected" not in incoming)
    return check.failures, check.total


def _manual_compact_never_gated_check() -> Tuple[int, int]:
    """An operator-typed `/compact` is never intercepted (fleet-config#1052).

    `chief_ops.py self-compact`'s refusals gate only the chief's *automatic*
    trigger. Nothing may stand between the operator and a manual compaction:
    no `PreCompact` hook is wired (one could block it), and every hook wired
    on the prompt path lets a bare `/compact` through untouched. Returns
    (failures, total)."""
    import tempfile as _tempfile
    from acceptance.shared import run

    check = _Checker()
    wired: set = set()
    for rel in ("settings.template.json", "codex-hooks.json"):
        data = json.loads((REPO / rel).read_text(encoding="utf-8"))
        check(f"manual compact: {rel} wires no PreCompact hook",
              not data.get("hooks", {}).get("PreCompact"))
        for event in ("UserPromptSubmit", "UserPromptExpansion"):
            for block in data.get("hooks", {}).get(event, []):
                for hook in block.get("hooks", []):
                    command = str(hook.get("command", ""))
                    found = re.search(r"-Hook\s+(\w+)", command) or re.search(r"hooks/(\w+)\.py", command)
                    if found:
                        wired.add(found.group(1))
    check("manual compact: the prompt-path hook set was found", bool(wired))
    skill = (REPO / ".claude" / "skills" / "chief" / "SKILL.md").read_text(encoding="utf-8")
    check("manual compact: the chief skill runs self-compact and says its refusals gate only the automatic trigger",
          "chief_ops.py self-compact" in skill and "gate only this automatic trigger" in skill)
    state = _tempfile.mkdtemp(prefix="manual_compact_")
    try:
        for hook in sorted(wired):
            for prompt in ("/compact", "/compact keep the in-flight lanes"):
                code, out, _err = run(hook, {"hook_event_name": "UserPromptSubmit", "prompt": prompt,
                                             "session_id": "manual-compact", "cwd": str(REPO)},
                                      extra_env={"CLAUDE_HOOKS_STATE_DIR": state})
                check(f"manual compact: {hook} lets {prompt!r} through (exit 0, no block)",
                      code == 0 and '"block"' not in out and '"deny"' not in out)
    finally:
        import shutil as _shutil
        _shutil.rmtree(state, ignore_errors=True)
    return check.failures, check.total


def _mermaid_check() -> Tuple[int, int]:
    """The Mermaid companion render (`render_mermaid.py`) can't silently go stale.

    Guards the text-native fleet map the same way `_fleet_toml_check` guards
    `fleet.data.js`:
      1. `system-map.mmd` is exactly what `render_mermaid.py` regenerates from
         the current `fleet.data.js` — a forgotten regen fails loud;
      2. the map stays out of always-on context: `global-CLAUDE.md` carries no
         generated flowchart, only a pointer to `system-map.mmd` (#897).
    Returns the failure count.
    """
    import importlib.util

    check = _Checker()

    rm_path = REPO / ".claude" / "skills" / "system-map" / "render_mermaid.py"
    spec = importlib.util.spec_from_file_location("system_map_render_mermaid", rm_path)
    rm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rm)

    data = rm.load_data((REPO / "architecture" / "fleet.data.js").read_text(encoding="utf-8"))
    rendered = rm.render(data)

    committed = (REPO / "architecture" / "system-map.mmd").read_text(encoding="utf-8")
    check("mermaid: system-map.mmd matches render_mermaid.py output", rendered == committed)

    claude_md = (REPO / "global-CLAUDE.md").read_text(encoding="utf-8")
    check(
        "mermaid: global-CLAUDE.md points at system-map.mmd instead of embedding the flowchart",
        "architecture/system-map.mmd" in claude_md
        and "system-map:mermaid" not in claude_md
        and "flowchart LR" not in claude_md,
    )

    return check.failures, check.total


def _system_map_whatchanged_check() -> Tuple[int, int]:
    """The /system-map week-over-week diff (.claude/skills/system-map/whatchanged.py).

    Pure-logic guard on the diff that feeds the one-line notify summary: added /
    removed repos are named, in-place edits are counted, a no-op week and a
    first run read sensibly. Returns the failure count.
    """
    import importlib.util

    check = _Checker()

    wc_path = REPO / ".claude" / "skills" / "system-map" / "whatchanged.py"
    spec = importlib.util.spec_from_file_location("system_map_whatchanged", wc_path)
    wc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wc)  # type: ignore[union-attr]

    prev = 'window.FLEET = {"web":[{"nm":"a","ds":"x"},{"nm":"b","ds":"y"}],"pipe":[{"nm":"c","ds":"z"}]};'
    # add d, remove b, edit a's description, c unchanged.
    cur = 'window.FLEET = {"web":[{"nm":"a","ds":"X2"},{"nm":"d","ds":"w"}],"pipe":[{"nm":"c","ds":"z"}]};'
    diff = wc.diff_fleet(prev, cur)
    check("system_map_whatchanged: detects an added repo", diff["added"] == ["d"])
    check("system_map_whatchanged: detects a removed repo", diff["removed"] == ["b"])
    check("system_map_whatchanged: counts edited cards, ignores unchanged", diff["updated"] == ["a"])

    # repo-keyed card (display name differs from repo) is keyed by `repo`.
    repo_prev = 'window.FLEET = {"web":[{"nm":"grocery","repo":"grocery-shopping-automation","ds":"x"}]};'
    repo_cur = 'window.FLEET = {"web":[]};'
    check("system_map_whatchanged: keys cards by repo-or-nm",
          wc.diff_fleet(repo_prev, repo_cur)["removed"] == ["grocery-shopping-automation"])

    check("system_map_whatchanged: format_line composes named adds/removes + count",
          wc.format_line(diff) == "+d, −b, 1 repo updated")
    check("system_map_whatchanged: empty diff reads 'no fleet changes'",
          wc.format_line({"added": [], "removed": [], "updated": []}) == "no fleet changes")
    check("system_map_whatchanged: no prior snapshot reads 'baseline'",
          wc.summarize(None, cur) == "baseline")

    return check.failures, check.total


def _config_map_check() -> Tuple[int, int, int]:
    """The /config-map data is fresh, and its week-over-week diff behaves.

    Guards the introspected config map (`.claude/skills/config-map`):
      1. **Advisory** — `config.data.js` is exactly what `build_data.py`
         regenerates. Same anti-staleness contract as `/system-map`, and the
         same scoping as `_fleet_toml_check`: `build_data.repo_skills()` /
         `coverage()` sweep every *sibling* repo's committed default branch, so
         a sister repo adding one `.claude/skills/` entry would otherwise turn
         this repo's gate red until the weekly `/config-map` run regenerated it
         (fleet-config#562). Reported, never failed; `/config-map` owns it.
      2. `whatchanged.py` pure-logic: adds/removes are named across every
         dimension (skills/hooks/matrix/conventions), edits are counted, repo
         keys collapse to a short label, and the no-op / first-run lines read
         sensibly. In-repo and deterministic — stays hard.
    Returns (failures, total, skipped).
    """
    import importlib.util

    check = _Checker()

    cm_dir = REPO / ".claude" / "skills" / "config-map"
    bd_spec = importlib.util.spec_from_file_location("config_map_build_data", cm_dir / "build_data.py")
    bd = importlib.util.module_from_spec(bd_spec)
    bd_spec.loader.exec_module(bd)  # type: ignore[union-attr]

    committed = (REPO / "architecture" / "config.data.js").read_text(encoding="utf-8")
    try:
        fresh = bd.regenerate() == committed
        regen_err = ""
    except Exception as exc:  # noqa: BLE001
        fresh, regen_err = False, f" ({exc})"
    check.advisory(
        f"config_map: config.data.js matches build_data.py output{regen_err}",
        fresh,
        "a sibling repo's committed skills/hooks moved ahead of the introspected snapshot.\n"
        "regenerate + commit with `/config-map`, or directly:\n"
        "E:/automation/fleet-config/.venv/Scripts/python.exe .claude/skills/config-map/build_data.py",
    )

    wc_spec = importlib.util.spec_from_file_location("config_map_whatchanged", cm_dir / "whatchanged.py")
    wc = importlib.util.module_from_spec(wc_spec)
    wc_spec.loader.exec_module(wc)  # type: ignore[union-attr]

    prev = ('window.CONFIG = {"skills_universal":[{"nm":"a","ds":"x"},{"nm":"b","ds":"y"}],'
            '"hooks":[{"nm":"h1","ds":"z"}]};')
    # add skill c, remove skill b, edit a's description, hook h1 unchanged.
    cur = ('window.CONFIG = {"skills_universal":[{"nm":"a","ds":"X2"},{"nm":"c","ds":"w"}],'
           '"hooks":[{"nm":"h1","ds":"z"}]};')
    diff = wc.diff_config(prev, cur)
    check("config_map_whatchanged: detects an added entry", diff["added"] == ["skill:c"])
    check("config_map_whatchanged: detects a removed entry", diff["removed"] == ["skill:b"])
    check("config_map_whatchanged: counts edited entries, ignores unchanged", diff["updated"] == ["skill:a"])
    check("config_map_whatchanged: format_line composes named adds/removes + count",
          wc.format_line(diff) == "+c, −b, 1 updated")

    # repo-specific skills flatten to repo:<repo>/<item>; the label drops the path.
    rp = 'window.CONFIG = {"skills_repo":[{"repo":"life-os","items":["j1","j2"]}]};'
    rc = 'window.CONFIG = {"skills_repo":[{"repo":"life-os","items":["j1"]}]};'
    check("config_map_whatchanged: keys repo skills by path, labels by short name",
          wc.format_line(wc.diff_config(rp, rc)) == "−j2")

    check("config_map_whatchanged: empty diff reads 'no config changes'",
          wc.format_line({"added": [], "removed": [], "updated": []}) == "no config changes")
    check("config_map_whatchanged: no prior snapshot reads 'baseline'",
          wc.summarize(None, cur) == "baseline")

    return check.failures, check.total, check.skipped


def _installer_symmetry_check() -> Tuple[int, int]:
    """install.ps1 and uninstall.ps1 stay each other's mirror (fleet-config#681).

    Two failures, one shape — the installer reporting success for work it did
    not do:

      1. **Self-elevation drops switches.** The UAC relaunch builds `$psArgs`
         by hand and the parent then `exit`s on the child's exit code, before
         ever reaching its own `Invoke-*Verification` calls. Any `[switch]`
         parameter not appended to `$psArgs` silently no-ops on exactly the
         machines that need the prompt — a fresh install.
      2. **Uninstall misses out-of-manifest state.** The manifest only records
         junctions/symlinks, so every `Install-*` that writes elsewhere (the
         OTel `$PROFILE` block, the agy plugin, the Copilot hook) needs its own
         `Remove-*`, or a "clean" uninstall leaves fleet-config's hooks live in
         another agent forever.

    Both are text-level, because there is no way to run either script in a test
    without touching this machine's real `~/.claude`.
    Returns (failures, total).
    """
    check = _Checker()

    install = (REPO / "install.ps1").read_text(encoding="utf-8")
    uninstall = (REPO / "uninstall.ps1").read_text(encoding="utf-8")

    # 1. every declared [switch] reaches the elevated child.
    param_block = install.split("param(", 1)[1].split(")", 1)[0]
    switches = re.findall(r"\[switch\]\$(\w+)", param_block)
    check("installer: install.ps1 declares at least one [switch] to forward",
          bool(switches))
    relaunch = install.split("$psArgs", 1)[1].split("Start-Process", 1)[0]
    unforwarded = [s for s in switches if f"-{s}" not in relaunch]
    check(f"installer: every [switch] is forwarded across the UAC relaunch (dropped: {unforwarded or 'none'})",
          not unforwarded)

    # 2. every out-of-manifest installer has an uninstall counterpart, and both
    #    are actually *called*, not merely defined.
    installers = re.findall(r"function\s+(Install-\w+)", install)
    check("installer: install.ps1 defines the out-of-manifest Install-* functions",
          len(installers) >= 3)
    for fn in installers:
        counterpart = fn.replace("Install-", "Remove-", 1)
        check(f"installer: {fn} has a {counterpart} counterpart in uninstall.ps1",
              f"function {counterpart}" in uninstall)
        # A defined-but-never-called Remove-* is the same silent no-op the
        # forwarding bug was: require an invocation line too.
        called = re.search(rf"^{counterpart}\s*$", uninstall, re.MULTILINE)
        check(f"installer: {counterpart} is actually invoked, not just defined",
              bool(called))

    return check.failures, check.total


def _readme_layout_check() -> Tuple[int, int]:
    """README's Layout tree really is the exhaustive inventory it reads as.

    fleet-config#565 (and #504 before it): the README's *prose* keeps up with
    each feature as it ships, the Layout block does not — so the two halves of
    one document ended up disagreeing about what exists (`agy/`,
    `copilot-hooks/`, and all three `session_state*` hooks were absent, and the
    count above the hook table was one short). A reader trusts that block as the
    file inventory, so a missing line reads as "this doesn't exist" rather than
    "this isn't listed" — which is worse than a plain omission. Three mechanical
    parts, so the next new directory or hook fails here instead of rotting:
      1. every top-level tracked directory is named in the Layout block;
      2. every `hooks/*.py` module is named in it;
      3. the "<N> hooks under `hooks/`" count matches the hook table's rows.

    Part 3 reads `docs/hooks.md`, not the README: fleet-config#648 relocated the
    hook reference catalogue there, leaving the README the layout tree and the
    install path. The invariant is unchanged — the count sentence and the table
    it introduces still have to agree — only the file that carries both moved.
    Returns (failures, total).
    """
    check = _Checker()

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    hooks_doc = (REPO / "docs" / "hooks.md").read_text(encoding="utf-8")

    # The fenced tree under "## Layout", up to the next top-level heading.
    after = readme.split("\n## Layout\n", 1)[1]
    layout = after.split("\n## ", 1)[0]

    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    top_dirs = sorted({
        line.split("/", 1)[0]
        for line in tracked.stdout.splitlines()
        if "/" in line
    })
    layout_top_dirs = _layout_top_dirs(layout)
    missing_dirs = [d for d in top_dirs if d not in layout_top_dirs]
    check(
        f"readme_layout: every top-level tracked directory is in the Layout tree "
        f"(missing: {missing_dirs or 'none'})",
        not missing_dirs,
    )

    # The matcher itself, against the shape that defeated the old substring
    # test (fleet-config#820): `codex/` named only in comment prose on other
    # entries' lines (`~/.codex/hooks`, `~/.codex/prompts`), with no tree
    # entry of its own anywhere in the block.
    _prose_only_layout = (
        "├── hooks/                          # junction → ~/.claude/hooks AND ~/.codex/hooks (Codex)\n"
        "├── commands/                       # junction → ~/.claude/commands AND ~/.codex/prompts (Codex prompts)\n"
    )
    check("readme_layout: the tree matcher ignores a directory named only in another entry's comment",
          "codex" not in _layout_top_dirs(_prose_only_layout))
    check("readme_layout: the tree matcher accepts a real top-level tree entry",
          "codex" in _layout_top_dirs(
              _prose_only_layout + "├── codex/                          # versioned Codex model policy data\n"))
    check("readme_layout: the tree matcher accepts a directory fused with a deeper path in one line",
          "agy" in _layout_top_dirs("├── agy/plugins/fleet-context-filter/   # installed by copy, not junction\n"))
    check("readme_layout: the tree matcher accepts a nested continuation entry too",
          "_lib.py" in _layout_tree_entries("├── hooks/\n│   ├── _lib.py                     # shared wire protocol\n"))

    hook_modules = sorted(p.name for p in (REPO / "hooks").glob("*.py"))
    missing_hooks = [h for h in hook_modules if h not in layout]
    check(
        f"readme_layout: every hooks/*.py module is in the Layout tree "
        f"(missing: {missing_hooks or 'none'})",
        not missing_hooks,
    )

    # "18 hooks under `hooks/` that ..." must match the table it introduces.
    m = re.search(r"^(\d+) hooks under `hooks/`", hooks_doc, re.M)
    rows = len(re.findall(r"^\| `[a-z0-9_]+\.py` \|", hooks_doc, re.M))
    claimed = int(m.group(1)) if m else -1
    check(
        f"readme_layout: docs/hooks.md's count matches its hook table "
        f"(claims {claimed}, table has {rows})",
        claimed == rows,
    )

    # The README must still route a reader to the relocated reference, or the
    # trimmed README becomes small at the cost of no longer orienting anyone.
    for target in ("docs/hooks.md", "docs/skills.md"):
        check(
            f"readme_layout: README points onward to {target}",
            f"]({target})" in readme,
        )

    return check.failures, check.total


def _settings_sync_split(
    missing: list[tuple[str, str]], live_hooks: Path
) -> Tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split template-wired-but-not-live `(event, hook)` pairs into
    `(unwired, pending_merge)` (fleet-config#942).

    `~/.claude/hooks` is a junction to the primary checkout, so a hook added on
    a branch has no module there until the change is on `main` and the primary
    has pulled it. Wiring it live before then makes `run-hook.ps1` fail with
    `Hook module not found` on every session fleet-wide; not wiring it made
    this guard red for the whole life of the branch. A pair whose module is
    absent from `live_hooks` is therefore `pending_merge` — its own state, not
    a pass. A pair whose module IS present but is still unwired is the drift
    this guard exists for, and stays `unwired`.
    """
    unwired = [pair for pair in missing if (live_hooks / f"{pair[1]}.py").exists()]
    pending = [pair for pair in missing if pair not in unwired]
    return unwired, pending


def _settings_sync_split_check() -> Tuple[int, int]:
    """Pin `_settings_sync_split`'s two states against a throwaway hooks dir,
    so the pending-merge escape can never swallow a present-but-unwired hook
    (fleet-config#942). Returns (failures, total)."""
    import tempfile

    check = _Checker()
    with tempfile.TemporaryDirectory() as tmp:
        hooks_dir = Path(tmp)
        (hooks_dir / "present_hook.py").write_text("", encoding="utf-8")
        present = ("SessionStart", "present_hook")
        absent = ("SessionStart", "absent_hook")
        check("settings_sync: a present-but-unwired hook is still unwired (fails)",
              _settings_sync_split([present], hooks_dir) == ([present], []))
        check("settings_sync: a hook whose module is not live yet is pending merge",
              _settings_sync_split([absent], hooks_dir) == ([], [absent]))
        check("settings_sync: a mixed set keeps the unwired half failing",
              _settings_sync_split([absent, present], hooks_dir) == ([present], [absent]))
    return check.failures, check.total


def _settings_template_sync_check() -> Tuple[int, int, int]:
    """Every hook wired in settings.template.json must also be wired in the live
    ~/.claude/settings.json.

    The live file is machine-local and NOT version-controlled (it carries
    permissions + secrets), so it can silently drift from the template — a hook
    can ship in the repo yet never actually run. This guard fails loudly when a
    template-wired `(event, hook)` is missing from the live file. Direction is
    template ⊆ live only: machine-local *extra* hooks are legitimate and don't
    fail. Prints exactly one line — always one check, whether it ran or not —
    and has two non-passing, non-failing states, both counted only in the
    separate Skipped counter so a run that couldn't verify never reads
    identical to one that verified and passed (fleet-config#461, #501):

    - SKIP — no live settings.json on this machine.
    - PEND — every missing hook's module is absent from the junctioned
      `~/.claude/hooks`, i.e. the hook is not on the live `main` yet and must
      not be wired live until it is (`_settings_sync_split`, fleet-config#942).
      Any missing hook whose module IS live still fails.
    """
    import re

    hook_re = re.compile(r"-Hook\s+(\w+)")

    def wired(path: Path) -> set[tuple[str, str]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        pairs: set[tuple[str, str]] = set()
        for event, blocks in data.get("hooks", {}).items():
            for block in blocks:
                for hook in block.get("hooks", []):
                    m = hook_re.search(hook.get("command", ""))
                    if m:
                        pairs.add((event, m.group(1)))
        return pairs

    live_path = Path.home() / ".claude" / "settings.json"
    if not live_path.exists():
        print("SKIP  settings_sync: no live ~/.claude/settings.json (skipped)")
        return 0, 0, 1

    template = wired(REPO / "settings.template.json")
    live = wired(live_path)
    unwired, pending = _settings_sync_split(
        sorted(template - live), Path.home() / ".claude" / "hooks")
    if unwired:
        print(f"FAIL  settings_sync: template hooks all wired live "
              f"(missing: {unwired}; pending merge: {pending or 'none'})")
        return 1, 1, 0
    if pending:
        print(f"PEND  settings_sync: not live on main yet, wire after merge "
              f"(pending merge: {pending}) (skipped)")
        return 0, 0, 1
    print("OK    settings_sync: template hooks all wired live (missing: none)")
    return 0, 1, 0
