"""The `/context-purge` run model: per-file probe states, merge outcomes and validation.

Pure functions over one run's `run.json`, rendered by `digest.py`. Unknown is a
first-class state here: a value the run did not record is None, never 0.
"""
from __future__ import annotations

from typing import Any, Optional


SHAPE_WEIGHT = {"large": 3, "medium": 2, "small": 1}


# ---- run-data access: unknown never becomes zero ---------------------------

def _num(value: Any) -> Optional[int]:
    """An int, or None when the run did not record one. Never coerces to 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def probe_state(entry: dict) -> str:
    """`probed` | `not-probed` | `unknown` for one file entry.

    The three-way split is the whole point: "0 files probed" and "probe
    coverage not recorded" are different facts.
    """
    if "probe" not in entry:
        return "unknown"
    probe = entry.get("probe")
    if probe is None:
        return "unknown"
    if not isinstance(probe, dict):
        return "unknown"
    if probe.get("ran") is True:
        return "probed"
    if probe.get("ran") is False:
        return "not-probed"
    return "unknown"


def probe_verdict(entry: dict) -> Optional[bool]:
    """True when the compressed file scored >= the control, None if unmeasured."""
    probe = entry.get("probe")
    if not isinstance(probe, dict) or probe.get("ran") is not True:
        return None
    compressed, control = _num(probe.get("compressed")), _num(probe.get("control"))
    if compressed is None or control is None:
        return None
    return compressed >= control


def rewritten_files(run: dict) -> list[tuple[dict, dict]]:
    return [(repo, f) for repo in run.get("repos", [])
            for f in repo.get("files", []) if f.get("action") == "rewritten"]


def headline(run: dict) -> dict:
    """The figures a reader needs before trusting anything else in the digest."""
    pairs = rewritten_files(run)
    tokens_removed = 0
    tokens_unknown = 0
    for _repo, f in pairs:
        before, after = _num(f.get("tokens_before")), _num(f.get("tokens_after"))
        if before is None or after is None:
            tokens_unknown += 1
        else:
            tokens_removed += before - after

    inventory_walked = 0
    inventory_unknown = 0
    for _repo, f in pairs:
        items = _num(f.get("inventory_items"))
        if items is None:
            inventory_unknown += 1
        else:
            inventory_walked += items

    states = [probe_state(f) for _repo, f in pairs]
    return {
        "tokens_removed": tokens_removed,
        "tokens_unknown": tokens_unknown,
        "files_rewritten": len(pairs),
        "files_assessed": sum(len(r.get("files", [])) for r in run.get("repos", [])),
        "inventory_walked": inventory_walked,
        "inventory_unknown": inventory_unknown,
        "probed": states.count("probed"),
        "not_probed": states.count("not-probed"),
        "probe_unknown": states.count("unknown"),
        "repos": len(run.get("repos", [])),
        "unreached": len(run.get("unreached", [])),
    }


def coverage_line(h: dict) -> str:
    """Probe coverage as a fraction, with unknowns kept as their own term."""
    if h["files_rewritten"] == 0:
        return "no files rewritten"
    line = f"{h['probed']}/{h['files_rewritten']} rewritten files probed"
    if h["probe_unknown"]:
        line += f", {h['probe_unknown']} not recorded"
    return line


def risk_score(repo: dict) -> tuple[int, str]:
    """Rank by what a lost directive would cost, not alphabetically."""
    risk = repo.get("risk") or {}
    score = 0
    if risk.get("always_on") is True:
        score += 3
    score += SHAPE_WEIGHT.get(str(risk.get("shape_change", "")).lower(), 0)
    checked = risk.get("checked")
    if checked is not True:
        score += 3  # nothing verified the result, or we do not know that it did
    label = "checked" if checked is True else ("unchecked" if checked is False else "unknown")
    return score, label


def ranked_repos(run: dict) -> list[dict]:
    return sorted(run.get("repos", []), key=lambda r: (-risk_score(r)[0], r.get("repo", "")))


def all_descriptions(run: dict) -> list[dict]:
    out = []
    for repo in run.get("repos", []):
        for d in repo.get("descriptions", []):
            out.append({**d, "repo": repo.get("repo", "?")})
    return out


def collect(run: dict, key: str) -> list[dict]:
    out = []
    for repo in run.get("repos", []):
        for item in repo.get(key, []):
            out.append({**item, "repo": repo.get("repo", "?")})
    return out


def untouched_files(run: dict) -> list[dict]:
    out = []
    for repo in run.get("repos", []):
        for f in repo.get("files", []):
            if f.get("action") in ("assessed-lean", "untouched"):
                out.append({**f, "repo": repo.get("repo", "?")})
    return out


# ---- merge outcome: what the run applied, and whether it was allowed to ------

MERGE_STATES = ("merged", "not-merged")
PRIMARY_STATES = ("fast-forwarded", "left")


def merge_record(repo: dict) -> Optional[dict]:
    """The repo's merge record, or None when the run did not record one."""
    merge = repo.get("merge")
    return merge if isinstance(merge, dict) else None


def merge_state(repo: dict) -> str:
    """`merged` | `not-merged` | `unknown` -- an absent record is never `merged`."""
    state = (merge_record(repo) or {}).get("state")
    return state if state in MERGE_STATES else "unknown"


def merge_blockers(repo: dict) -> list[str]:
    """Why this repo's rewrite may NOT be merged. Empty = eligible.

    The deterministic half of the auto-merge go/no-go (fleet-config#1312): a
    worker runs it (`digest.py merge-check`) before `gh pr merge`, and
    `validate` re-applies it to every repo recorded as merged, so a rewrite
    that skipped the harness cannot reach `main` and still publish a clean
    digest. Anything the run could not establish blocks -- unknown is not a pass.
    """
    blockers: list[str] = []
    if not repo.get("pr"):
        blockers.append("no PR recorded")
    if (repo.get("risk") or {}).get("checked") is not True:
        blockers.append("check.py, audit.py and the repo gate not confirmed passing "
                        "(risk.checked is not true)")
    rewritten = [f for f in repo.get("files", []) if f.get("action") == "rewritten"]
    if not rewritten:
        blockers.append("no rewritten files")
    for f in rewritten:
        path = f.get("path", "?")
        items, done = _num(f.get("inventory_items")), _num(f.get("inventory_discharged"))
        if items is None or done is None:
            blockers.append(f"{path}: directive-inventory result not recorded")
        elif done != items:
            blockers.append(f"{path}: inventory {done}/{items} discharged")
        state = probe_state(f)
        if state == "unknown":
            blockers.append(f"{path}: probe not recorded (ran=false is a decision, null is not)")
        elif state == "probed":
            verdict = probe_verdict(f)
            if verdict is None:
                blockers.append(f"{path}: probe scores not recorded")
            elif verdict is False:
                blockers.append(f"{path}: probe regression (compressed scored below control)")
    return blockers


def merged_repos(run: dict) -> list[dict]:
    return [r for r in run.get("repos", []) if merge_state(r) == "merged"]


def blast_radius(repo: str, path: str) -> str:
    """Which agents and sessions load a context file, derived from its path.

    Pure path logic so the digest states it the same way every week and cannot
    drift from the layout (`install.ps1` junctions `skills/` into every agent).
    """
    name = path.rsplit("/", 1)[-1]
    if repo == "fleet-config" and path == "global-CLAUDE.md":
        return "every agent in every session, fleet-wide"
    if name == "SKILL.md":
        skill = path.rsplit("/", 2)[-2] if path.count("/") >= 1 else "?"
        if repo == "fleet-config" and path.startswith("skills/"):
            return (f"global skill: its description is always-on in every session "
                    f"of every repo; the body loads when `/{skill}` runs")
        return (f"`/{skill}` in `{repo}`: description always-on in `{repo}` sessions, "
                f"body loads when it runs")
    if name == "CLAUDE.md":
        return f"every session started in `{repo}`"
    return f"sessions that read `{path}` in `{repo}`"


def _lines(f: dict) -> str:
    before, after = _num(f.get("lines_before")), _num(f.get("lines_after"))
    if before is None or after is None:
        return "not recorded"
    return f"{before:,} to {after:,} ({after - before:+,})"


def _revert(repo: dict) -> str:
    merge = merge_record(repo)
    sha = (merge or {}).get("sha")
    if merge_state(repo) != "merged" or not sha:
        return "n/a"
    return f"`git revert {str(sha)[:10]}` in `{repo.get('repo', '?')}`"


def _merge_cell(repo: dict) -> str:
    state = merge_state(repo)
    merge = merge_record(repo) or {}
    if state == "merged":
        return "merged"
    if state == "not-merged":
        return f"NOT merged -- {merge.get('reason', 'reason not recorded')}"
    return "not recorded"


def _primary_cell(repo: dict) -> str:
    primary = (merge_record(repo) or {}).get("primary")
    if merge_state(repo) != "merged":
        return "n/a"
    if not isinstance(primary, dict) or primary.get("state") not in PRIMARY_STATES:
        return "not recorded"
    if primary["state"] == "fast-forwarded":
        return "fast-forwarded"
    return f"left -- {primary.get('reason', 'reason not recorded')}"


def merge_summary(run: dict) -> dict:
    """Counts the headline and chat need; unknown stays its own term."""
    repos = [r for r in run.get("repos", []) if r.get("status") != "skipped"]
    states = [merge_state(r) for r in repos]
    primaries = [(merge_record(r) or {}).get("primary") for r in merged_repos(run)]
    return {
        "candidates": len(repos),
        "merged": states.count("merged"),
        "not_merged": states.count("not-merged"),
        "unknown": states.count("unknown"),
        "primaries_left": sum(1 for p in primaries
                              if isinstance(p, dict) and p.get("state") == "left"),
        "primaries_unknown": sum(1 for p in primaries
                                 if not isinstance(p, dict)
                                 or p.get("state") not in PRIMARY_STATES),
    }


# ---- validation -------------------------------------------------------------

def _validate_merge(repo: dict, name: str) -> list[str]:
    errors: list[str] = []
    status = repo.get("status")
    if status == "shipped" and "merge" not in repo:
        errors.append(f"{name}: a shipped repo must carry a 'merge' key "
                      f"(null is allowed and means 'not recorded')")
    merge = repo.get("merge")
    if merge is None:
        return errors
    if not isinstance(merge, dict):
        return errors + [f"{name}: merge must be an object or null"]
    state = merge.get("state")
    if state not in MERGE_STATES:
        errors.append(f"{name}: merge.state must be merged|not-merged, got {state!r}")
    elif state == "merged":
        if not merge.get("sha"):
            errors.append(f"{name}: merged but no merge.sha -- the revert pointer is the "
                          f"point of reporting a merge")
        if status != "shipped":
            errors.append(f"{name}: merge.state=merged but status is {status!r} -- "
                          f"'shipped' means merged")
        for why in merge_blockers(repo):
            errors.append(f"{name}: merged without passing validation -- {why}")
    else:
        if not merge.get("reason"):
            errors.append(f"{name}: not-merged must say why (merge.reason)")
        if status == "shipped":
            errors.append(f"{name}: status=shipped but merge.state=not-merged -- contradictory")
    primary = merge.get("primary")
    if primary is not None:
        if state != "merged":
            errors.append(f"{name}: merge.primary recorded for a repo that was not merged")
        elif not isinstance(primary, dict) or primary.get("state") not in PRIMARY_STATES:
            errors.append(f"{name}: merge.primary.state must be fast-forwarded|left")
        elif primary["state"] == "left" and not primary.get("reason"):
            errors.append(f"{name}: a primary left behind must say why (merge.primary.reason)")
    return errors


def validate(run: Any) -> list[str]:
    """Structural problems that would make the digest lie. Empty = usable."""
    errors: list[str] = []
    if not isinstance(run, dict):
        return ["run data is not a JSON object"]
    if not run.get("run_id"):
        errors.append("run_id is missing")
    status = run.get("status")
    if status not in ("complete", "partial"):
        errors.append(f"status must be 'complete' or 'partial', got {status!r}")
    if not isinstance(run.get("repos"), list):
        errors.append("repos must be a list")
        return errors
    if status == "partial" and not run.get("unreached"):
        errors.append("status=partial but no unreached repos are named -- a partial run "
                      "must say which repos it did not reach")
    if status == "complete" and run.get("unreached"):
        errors.append("status=complete but unreached repos are listed -- contradictory")
    for repo in run["repos"]:
        name = repo.get("repo", "<unnamed>")
        if not repo.get("repo"):
            errors.append("a repo entry has no name")
        if repo.get("status") not in ("shipped", "skipped", "failed"):
            errors.append(f"{name}: status must be shipped|skipped|failed, "
                          f"got {repo.get('status')!r}")
        for f in repo.get("files", []):
            if not f.get("path"):
                errors.append(f"{name}: a file entry has no path")
            if f.get("action") not in ("rewritten", "assessed-lean", "untouched"):
                errors.append(f"{name}/{f.get('path')}: action must be "
                              f"rewritten|assessed-lean|untouched, got {f.get('action')!r}")
            if f.get("action") == "rewritten" and "probe" not in f:
                errors.append(f"{name}/{f.get('path')}: rewritten file must carry a "
                              f"'probe' key (null is allowed and means 'not recorded')")
        errors.extend(_validate_merge(repo, name))
    return errors
