"""Deterministic identity for audit-managed GitHub issues.

The audit skills (`/codebase-audit`, `/audit-fleet`) keep exactly one open issue
per *kind* per repo: a ledger, a digest, and one per finding bucket. Idempotency
used to ride on LLM judgment, which slips under the unattended `claude -p` path
and spawns duplicates. This helper moves the create-vs-reuse decision into Python,
keyed on a hidden marker in the issue body, so duplication is structurally
impossible. Same principle as ``hooks/notify_complete.py``: the idempotency-
critical decision lives here, not in the model.

It does **not** decide *content* — the skill builds the (merged) body and hands it
here. This helper only answers "which issue is THE one for (repo, kind)?" and
writes to it, collapsing any strays.

Two subcommands:

  get    --repo OWNER/NAME --kind KIND
         -> prints JSON {"number": N|null, "body": "...", "duplicates": [n,...]}
         The skill reads the existing body, merges its findings, then calls upsert.

  upsert --repo OWNER/NAME --kind KIND --title T --body-file F [--label L]
         0 matches -> create · 1 -> edit · >1 -> edit lowest, close the rest as
         duplicates. Stamps the marker. Prints the canonical issue URL.

Identity is the marker `<!-- audit-managed: kind=<kind> -->`. Pre-existing issues
that predate the marker are adopted by their stable title (see ``title_matches``),
stamped on first edit — so no separate migration pass is needed.

stdlib + the `gh` CLI only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile

KINDS = (
    "ledger",
    "digest",
    "duplication",
    "stale",
    "claude-md-drift",
    "maintainability",
    "bug",
    "documentation",
)

_MARKER_RE = re.compile(
    r"^[ \t]*<!--[ \t]*audit-managed:[ \t]*kind=([\w-]+)[ \t]*-->[ \t]*$", re.MULTILINE
)


# ---- pure helpers (unit-tested without gh) --------------------------------

def marker_for(kind: str) -> str:
    return f"<!-- audit-managed: kind={kind} -->"


def has_marker(body: str, kind: str) -> bool:
    return any(m.group(1) == kind for m in _MARKER_RE.finditer(body or ""))


def ensure_marker(body: str, kind: str) -> str:
    """Return body with exactly the correct marker as its first line.

    Strips any pre-existing audit-managed marker(s) first so re-stamping is
    idempotent and never accumulates markers.
    """
    stripped = _MARKER_RE.sub("", body or "").lstrip("\n")
    return f"{marker_for(kind)}\n\n{stripped}" if stripped else f"{marker_for(kind)}\n"


def title_matches(title: str, kind: str) -> bool:
    """Adopt pre-marker issues by their stable title."""
    t = (title or "").strip()
    if kind == "ledger":
        return t == "codebase-audit ledger"
    if kind == "digest":
        return t == "audit-fleet digest state"
    # bucket kinds: "audit: <kind> findings ..." (trailing count suffix tolerated)
    return re.match(r"^audit:\s*" + re.escape(kind) + r"\s+findings\b", t) is not None


def plan(issues: list[dict], kind: str) -> tuple[int | None, list[int]]:
    """Given open issues (dicts with number/title/body), decide keep + close.

    A candidate matches the marker OR the stable title. Keep the lowest number
    (the original); everything else is a stray to collapse.
    """
    candidates = sorted(
        i["number"]
        for i in issues
        if has_marker(i.get("body", ""), kind) or title_matches(i.get("title", ""), kind)
    )
    if not candidates:
        return None, []
    return candidates[0], candidates[1:]


# ---- gh plumbing ----------------------------------------------------------

def _run(args: list[str]) -> subprocess.CompletedProcess:
    # Force UTF-8: issue bodies routinely contain non-ASCII (em dashes, emoji),
    # and the Windows default (cp1252) raises UnicodeDecodeError mid-read, which
    # would crash the unattended weekly run. errors="replace" never throws.
    return subprocess.run(
        ["gh", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _gh(args: list[str]) -> str:
    r = _run(args)
    if r.returncode != 0:
        sys.stderr.write(r.stderr or "")
        raise SystemExit(f"gh {' '.join(args)} failed (exit {r.returncode})")
    return (r.stdout or "").strip()


def _list_open(repo: str) -> list[dict]:
    out = _gh([
        "issue", "list", "--repo", repo, "--state", "open",
        "--limit", "300", "--json", "number,title,body",
    ])
    return json.loads(out) if out else []


def _write_tmp(body: str) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    f.write(body)
    f.close()
    return f.name


def _ensure_label(repo: str, label: str) -> None:
    # idempotent: a duplicate-create just fails and is ignored
    _run(["label", "create", label, "--repo", repo])


# ---- subcommands ----------------------------------------------------------

def cmd_get(repo: str, kind: str) -> None:
    keep, dupes = plan(_list_open(repo), kind)
    body = ""
    if keep is not None:
        body = _gh(["issue", "view", str(keep), "--repo", repo, "--json", "body", "-q", ".body"])
    print(json.dumps({"number": keep, "body": body, "duplicates": dupes}))


def cmd_upsert(repo: str, kind: str, title: str, body: str, label: str | None) -> None:
    keep, dupes = plan(_list_open(repo), kind)
    tmp = _write_tmp(ensure_marker(body, kind))

    if label:
        _ensure_label(repo, label)

    if keep is None:
        create = ["issue", "create", "--repo", repo, "--title", title,
                  "--body-file", tmp, "--assignee", "@me"]
        if label:
            create += ["--label", label]
        url = _gh(create)
    else:
        edit = ["issue", "edit", str(keep), "--repo", repo, "--title", title, "--body-file", tmp]
        if label:
            edit += ["--add-label", label]
        _gh(edit)
        url = _gh(["issue", "view", str(keep), "--repo", repo, "--json", "url", "-q", ".url"])
        for n in dupes:
            _ensure_label(repo, "duplicate")
            _run(["issue", "edit", str(n), "--repo", repo, "--add-label", "duplicate"])
            _gh([
                "issue", "close", str(n), "--repo", repo,
                "--comment",
                f"Collapsed into #{keep} — one audit issue per type per repo "
                f"(see skills/_lib/audit_issue.py).",
            ])

    print(url)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Deterministic upsert for audit-managed issues.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get")
    g.add_argument("--repo", required=True)
    g.add_argument("--kind", required=True, choices=KINDS)

    u = sub.add_parser("upsert")
    u.add_argument("--repo", required=True)
    u.add_argument("--kind", required=True, choices=KINDS)
    u.add_argument("--title", required=True)
    u.add_argument("--body-file", required=True)
    u.add_argument("--label", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "get":
        cmd_get(args.repo, args.kind)
    elif args.cmd == "upsert":
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
        cmd_upsert(args.repo, args.kind, args.title, body, args.label)


if __name__ == "__main__":
    main()
