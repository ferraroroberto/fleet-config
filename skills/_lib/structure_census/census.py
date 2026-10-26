"""Fleet code-structure census: measure every repo, or compare two measurements.

  census.py run --out <file.json> [--as-of <ISO time>] [--projects-toml <path>]
  census.py compare <before.json> <after.json> [--json <file.json>]

`run` measures every repo with a `cwd_prefix` in `hooks/projects.toml`, dormant ones
included and marked, each at `origin/main` as last fetched (`--as-of` re-runs an earlier
census at the commits that were current then). `compare` prints the headline, per-shape
and per-repo before-and-after tables as Markdown, and `--json` also writes them as data.
Definitions and how to read each number: docs/structure-census.md. Exit 0 on success,
2 on a usage error or an unreadable input file.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fleet_core import fleet_repo_scan  # noqa: E402
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from structure_census import repo_read  # noqa: E402

log = logging.getLogger("structure_census")

CENSUS_VERSION = 1
# Shape ids from `.fleet.toml`, folded into the groups the reports compare.
SHAPE_GROUPS = {
    "webapp-tray": "Web app", "service": "Web app", "streamlit": "Streamlit",
    "pipeline-cli": "Pipeline / CLI", "skills-governance": "Skills / governance",
    "agent-workspace": "Skills / governance", "node": "Node", "static-site": "Static site",
}
# Headline metrics: name -> how one repo record yields it.
METRICS = {
    "loc": lambda r: r["loc"],
    "loc_vendored": lambda r: r["loc_vendored"],
    "code_files": lambda r: r["files"],
    "files_over_1000": lambda r: len(r["over_1k"]),
    "files_over_2000": lambda r: sum(1 for _p, n, _k in r["over_1k"] if n > repo_read.BIG_FILE_LIMIT),
    "functions_over_100": lambda r: r["fns_over"]["100"],
    "functions_over_200": lambda r: r["fns_over"]["200"],
    "functions_300_plus": lambda r: len(r["fns_300"]),
    "files_900_to_1000": lambda r: r["files_near_limit"],
    "functions_90_to_100": lambda r: r["fns_near_limit"],
    "flat_dirs_over_40": lambda r: len(r["flat_dirs"]),
    "src_app_imports": lambda r: len(r["layer_imports"]),
    "structure_ratchet": lambda r: int("structure_ratchet" in r["ratchets"].get("hits", {})),
    "ratchets_enabled": lambda r: len(r["ratchets"].get("hits", {})),
    "no_gate_found": lambda r: int(not r["gate_files"] and not r["ratchets"].get("hits")),
}


def run(out: Path, as_of: Optional[str], projects_toml: Optional[Path]) -> int:
    """Measure the whole fleet into `out`."""
    every = fleet_repo_scan.fleet_repos(projects_toml, include_ignored=True)
    active = fleet_repo_scan.fleet_repos(projects_toml)
    records = []
    for name, path in sorted(every.items()):
        record = repo_read.census_repo(name, path, as_of)
        record["dormant"] = name not in active
        log.info("%s %s %s", "✅" if record["status"] == "ok" else "⚠️", name,
                 record.get("sha", "")[:7] or record.get("reason", ""))
        records.append(record)
    doc = {
        "version": CENSUS_VERSION, "as_of": as_of,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repos": records,
    }
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    log.info("✅ %d repos, %d unknown -> %s", len(records), sum(r["status"] != "ok" for r in records), out)
    return 0


def _totals(records: list[dict]) -> dict:
    return {name: sum(fn(r) for r in records) for name, fn in METRICS.items()}


def _rows(before: dict, after: dict, names: list[str]) -> dict:
    """`{metric: [before, after, change]}` over the repos in `names`."""
    tb = _totals([before[n] for n in names])
    ta = _totals([after[n] for n in names])
    return {m: [tb[m], ta[m], ta[m] - tb[m]] for m in METRICS}


def _big_files(records: dict) -> list:
    return sorted(
        ([n, repo, path, kind] for repo, r in records.items() for path, n, kind in r["over_1k"] if n > repo_read.BIG_FILE_LIMIT),
        key=lambda row: (-row[0], row[1], row[2]),
    )


def compare(before_doc: dict, after_doc: dict) -> dict:
    """Headline, per-shape and per-repo before-and-after figures for the repos measured in both."""
    before = {r["repo"]: r for r in before_doc["repos"] if r["status"] == "ok"}
    after = {r["repo"]: r for r in after_doc["repos"] if r["status"] == "ok"}
    both = sorted(set(before) & set(after))
    active = [n for n in both if not after[n]["dormant"]]
    groups: dict[str, list[str]] = {}
    for name in both:
        groups.setdefault(SHAPE_GROUPS.get(after[name]["shape"], "Other"), []).append(name)
    return {
        "before": {"as_of": before_doc.get("as_of"), "measured_at": before_doc.get("measured_at")},
        "after": {"as_of": after_doc.get("as_of"), "measured_at": after_doc.get("measured_at")},
        "repos_compared": both,
        "dormant": sorted(n for n in both if after[n]["dormant"]),
        "unknown": sorted(r["repo"] for r in after_doc["repos"] if r["status"] != "ok"),
        "headline_all": _rows(before, after, both),
        "headline_active": _rows(before, after, active),
        "by_shape": {g: {"repos": names, **_rows(before, after, names)} for g, names in sorted(groups.items())},
        "by_repo": {
            n: {"shape": after[n]["shape"], "dormant": after[n]["dormant"],
                "before_sha": before[n]["sha"], "after_sha": after[n]["sha"],
                "ratchets": after[n]["ratchets"], **_rows(before, after, [n])}
            for n in both
        },
        "big_files": {"before": _big_files(before), "after": _big_files(after)},
        "functions_300_plus": {
            "before": sorted(([f[0], n, f[1]] for n in both for f in before[n]["fns_300"]), key=lambda x: -x[0]),
            "after": sorted(([f[0], n, f[1]] for n in both for f in after[n]["fns_300"]), key=lambda x: -x[0]),
        },
        "flat_dirs": {"before": {n: before[n]["flat_dirs"] for n in both if before[n]["flat_dirs"]},
                      "after": {n: after[n]["flat_dirs"] for n in both if after[n]["flat_dirs"]}},
    }


def _table(title: str, rows: dict) -> list[str]:
    out = [f"### {title}", "", "| metric | before | after | change |", "|---|---:|---:|---:|"]
    out += [f"| {m} | {rows[m][0]:,} | {rows[m][1]:,} | {rows[m][2]:+,} |" for m in METRICS]
    return out + [""]


def render(result: dict) -> str:
    """The comparison as Markdown."""
    lines = [
        f"Compared {len(result['repos_compared'])} repos ({len(result['dormant'])} dormant: "
        f"{', '.join(result['dormant']) or 'none'}); unknown now: {', '.join(result['unknown']) or 'none'}.", "",
    ]
    lines += _table("All compared repos", result["headline_all"])
    lines += _table("Active repos only", result["headline_active"])
    for group, rows in result["by_shape"].items():
        lines += _table(f"{group} ({len(rows['repos'])})", rows)
    lines += ["### Per repo (before -> after)", "",
              "| repo | loc | >1k | >2k | fn>100 | fn300+ | dirs>40 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, rows in result["by_repo"].items():
        cells = [f"{rows[m][0]:,}->{rows[m][1]:,}" for m in
                 ("loc", "files_over_1000", "files_over_2000", "functions_over_100", "functions_300_plus", "flat_dirs_over_40")]
        lines.append(f"| {name}{' (dormant)' if rows['dormant'] else ''} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _load(path: str) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"❌ {path}: {exc}") from exc
    if doc.get("version") != CENSUS_VERSION:
        raise SystemExit(f"❌ {path}: not a census file of version {CENSUS_VERSION}")
    return doc


def main(argv: Optional[list[str]] = None) -> int:
    ensure_utf8_stdio()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="measure every fleet repo")
    p_run.add_argument("--out", required=True, type=Path)
    p_run.add_argument("--as-of", help="measure each repo at its last commit at or before this time")
    p_run.add_argument("--projects-toml", type=Path, help="the fleet list (default: hooks/projects.toml)")
    p_cmp = sub.add_parser("compare", help="compare two census files")
    p_cmp.add_argument("before")
    p_cmp.add_argument("after")
    p_cmp.add_argument("--json", type=Path, help="also write the comparison as JSON")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        return run(args.out, args.as_of, args.projects_toml)
    try:
        result = compare(_load(args.before), _load(args.after))
    except SystemExit as exc:
        log.error("%s", exc)
        return 2
    if args.json:
        args.json.write_text(json.dumps(result, indent=1), encoding="utf-8")
    sys.stdout.write(render(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
