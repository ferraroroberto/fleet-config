"""`upstream_sync` command line: the deterministic half of `/upstream-sync`.

  init <url> [--branch B] [--dry-run]
                            add the fetch-only source remote, set the watermark to its head
                            (--dry-run: report the head, write nothing)
  status [--ref REF]        the [upstream] table in the working tree, or as committed on REF
  survey [--against REF]    fetch, then one ITEM line per source item after the watermark,
                            `present=yes` when REF (default HEAD) already carries its patch
  advance <sha> --outcome merged|skipped [--reason R]
                            record one item; the watermark moves only over a contiguous run

Every command takes `--repo PATH` (default `.`) and prints `KEY=value` lines. Exit 0 done,
1 held (the watermark could not move onto the named item, or the table is absent on
`status`), 2 refused call, 3 unknown (git or the table could not be read). Contract:
`docs/skills/upstream-sync.md`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

_LIB = Path(__file__).resolve().parent.parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
from fleet_core.utf8_stdio import ensure_utf8_stdio  # noqa: E402
from git_run import run_git  # noqa: E402

from upstream_sync import ledger, source  # noqa: E402

EXIT_OK, EXIT_HELD, EXIT_REFUSED, EXIT_UNKNOWN = 0, 1, 2, 3


def _print_state(state: ledger.UpstreamState) -> None:
    print(f"URL={state.url}")
    print(f"BRANCH={state.branch}")
    print(f"SYNCED_SHA={state.synced_sha}")
    print(f"SKIPPED={len(state.skipped)}")


def _load(repo: Path) -> Optional[ledger.UpstreamState]:
    state, status = ledger.read_state(repo)
    if status != "ok":
        print(f"UPSTREAM={status}")
        if status == "absent":
            print("TRY=run `init <source-url>` first")
    return state


def _licence_line(repo: Path, branch: str) -> None:
    name, reuse = source.classify_licence(source.licence_text(repo, branch))
    print(f"LICENSE={name} REUSE={reuse}")


def cmd_init(repo: Path, url: str, branch: str, dry_run: bool) -> int:
    state, status = ledger.read_state(repo)
    if status != "absent":
        print(f"UPSTREAM={status} REFUSED=already initialised; the watermark is never reset")
        return EXIT_REFUSED
    source.ensure_remote(repo, url)
    head = source.fetch(repo, branch)
    print(f"SOURCE_HEAD={head}")
    if dry_run:
        print("SYNCED_SHA=unset WRITE=skipped (dry run)")
    else:
        ledger.write_state(repo, ledger.UpstreamState(url=url, branch=branch, synced_sha=head))
        print(f"SYNCED_SHA={head}")
    _licence_line(repo, branch)
    return EXIT_OK


def cmd_status(repo: Path, ref: Optional[str]) -> int:
    if ref:
        done = run_git(["-C", str(repo), "show", f"{ref}:{ledger.FILENAME}"])
        state, status = ledger.parse_text(done.stdout if done.returncode == 0 else None)
    else:
        state, status = ledger.read_state(repo)
    print(f"UPSTREAM={status}")
    if state:
        _print_state(state)
        return EXIT_OK
    return EXIT_HELD if status == "absent" else EXIT_UNKNOWN


def cmd_survey(repo: Path, against: str) -> int:
    state = _load(repo)
    if not state:
        return EXIT_REFUSED
    source.ensure_remote(repo, state.url)
    head = source.fetch(repo, state.branch)
    if not source.is_ancestor(repo, state.synced_sha, state.branch):
        print(f"WATERMARK=unknown reason=synced_sha {state.synced_sha} is not on the source branch (history rewritten?)")
        return EXIT_UNKNOWN
    _print_state(state)
    print(f"SOURCE_HEAD={head}")
    _licence_line(repo, state.branch)
    items = source.survey(repo, state.synced_sha, state.branch, against)
    print(f"PENDING={len(items)}")
    skipped = state.skipped_shas()
    for i, item in enumerate(items, 1):
        print(f"ITEM {i} sha={item.sha} pr={item.pr if item.pr is not None else '-'} "
              f"merge={'yes' if item.merge else 'no'} present={'yes' if item.present else 'no'} "
              f"skipped={'yes' if item.sha in skipped else 'no'} files={','.join(item.files) or '-'} "
              f"subject={item.subject}")
    return EXIT_OK


def cmd_advance(repo: Path, sha: str, outcome: str, reason: Optional[str]) -> int:
    if outcome == "skipped" and not (reason and reason.strip()):
        print("REFUSED=a skip needs --reason")
        return EXIT_REFUSED
    state = _load(repo)
    if not state:
        return EXIT_REFUSED
    resolved = run_git(["-C", str(repo), "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"])
    full = resolved.stdout.strip()
    order = source.pending(repo, state.synced_sha, state.branch)
    if resolved.returncode != 0 or full not in order:
        print(f"REFUSED={sha} is not a source item after the watermark {state.synced_sha}")
        return EXIT_REFUSED
    if outcome == "skipped" and full not in state.skipped_shas():
        state.skipped.append(ledger.Skip(sha=full, reason=reason.strip()))
    merged = {full} if outcome == "merged" else set()
    new = ledger.advance_watermark(state.synced_sha, order, merged, state.skipped_shas())
    moved = new != state.synced_sha
    state.synced_sha = new
    ledger.write_state(repo, state)
    print(f"WATERMARK={new} moved={'yes' if moved else 'no'}")
    reached = order[: order.index(new) + 1] if new in order else []
    if full not in reached:
        blocker = order[len(reached)]
        print(f"WATERMARK=held before={blocker} reason=an earlier item is neither merged nor skipped")
        return EXIT_HELD
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(prog="upstream_sync", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("init", "status", "survey", "advance"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=".")
        if name == "init":
            p.add_argument("url")
            p.add_argument("--branch", default="main")
            p.add_argument("--dry-run", action="store_true")
        if name == "survey":
            p.add_argument("--against", default="HEAD")
        if name == "status":
            p.add_argument("--ref")
        if name == "advance":
            p.add_argument("sha")
            p.add_argument("--outcome", required=True, choices=("merged", "skipped"))
            p.add_argument("--reason")
    args = parser.parse_args(list(argv) if argv is not None else None)
    repo = Path(args.repo).resolve()
    try:
        if args.cmd == "init":
            return cmd_init(repo, args.url, args.branch, args.dry_run)
        if args.cmd == "status":
            return cmd_status(repo, args.ref)
        if args.cmd == "survey":
            return cmd_survey(repo, args.against)
        return cmd_advance(repo, args.sha, args.outcome, args.reason)
    except (source.SourceError, ValueError) as exc:
        print(f"UNKNOWN={exc}")
        return EXIT_UNKNOWN


if __name__ == "__main__":
    sys.exit(main())
