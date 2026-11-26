# Proposal reports

The fleet's standard for any report that proposes changes (retro findings, improvement ideas, audit suggestions): one page with every suggestion scored for impact and effort, and one Board question per suggestion so the owner accepts or declines each one on its own. Nothing is filed or built from the report itself; an accepted ask is acted on afterwards, through `/issue-add` like any other work.

The code is `skills/_lib/proposal_report/` (stdlib only).

## Input

One JSON file:

```json
{
  "id": "retro-2026-w48",
  "title": "Week 48 improvement proposals",
  "subtitle": "optional context line; default: counts",
  "suggestions": [
    {
      "id": "cache-board",
      "title": "Cache the Board's first load",
      "summary": "Serve the last snapshot while the fresh one builds.",
      "repo": "app-launcher",
      "category": "speed",
      "impact": 5,
      "effort": 2,
      "evidence": "optional: what the score rests on",
      "home": "optional: where the change would live"
    }
  ]
}
```

- `id` (report) is lowercase letters, digits and hyphens; it is part of every ask id, so a new report needs a new id, and the same report re-run keeps it.
- Each suggestion `id` is lowercase letters, digits and hyphens (at most 48), unique in the report. It becomes the page anchor `#s-<id>`.
- `impact` and `effort` are whole numbers 1-5. `repo` must be a repo name the Board accepts.

## Quadrants

The midline sits between 2 and 3 on both axes, so a 3 counts as high impact and as real effort:

| | effort 1-2 | effort 3-5 |
|---|---|---|
| impact 3-5 | Quick win | Big bet |
| impact 1-2 | Fill-in | Money pit |

## The page

```
E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/proposal_report/cli.py render <report.json> --out <page.html>
```

Prints `PAGE=<path> items=<n> quick_wins=<n>`. The page is one self-contained file (inline CSS and script, no fetches) in the fleet design system's tokens, light and dark from the OS setting plus a toggle, laid out for a 390px phone. It holds:

- the impact-vs-effort chart, quick-win quadrant tinted, one numbered dot per suggestion linking to its row; suggestions with the same scores fan out instead of covering each other;
- filter chips for repo, category and quadrant: chips in one group widen (either), groups narrow (both), and one filter hides the same items in the chart and the list;
- the list, sortable by report order, impact, effort or impact per effort.

The chart and list are rendered in Python, so the page reads with scripts off; the script only filters and sorts.

Publish the page somewhere the owner's phone opens (an Artifact is the usual home) before posting asks: the asks link to it.

## The asks

```
E:/automation/fleet-config/.venv/Scripts/python.exe skills/_lib/proposal_report/cli.py ask <report.json> --page-url <url> [--dry-run]
```

One Board question per suggestion through `chief_ops.py plan ask`, so the journal records each one:

- question `Take up "<title>"?`, detail with the summary, scores, quadrant, repo and the link `<url>#s-<id>`;
- options Accept, Decline, Later; a quick win recommends Accept, a money pit Decline, the other two nothing;
- ask id `pr-<12 hex>` from the report and suggestion ids, so it is the same on every run.

A suggestion is skipped when its ask is still open on the plan (`already-open`) or the ledger `proposal-asks-ledger.json` in the hooks state dir shows it was posted before (`already-asked`): an answered ask leaves the plan, so the plan alone would let it repeat. A ledger that exists but cannot be read stops the run rather than risk repeats.

`--dry-run` lists the asks it would post and writes nothing. Tests and trials use it, or `--plan-file` and `--ledger` pointing at throwaway files; never a real report against the live Board.

Output is one `ASK=<state> id=<id> item=<suggestion> link=<url#anchor>` line per suggestion and an `ASKS=` count line. Exit 0 when nothing failed, 1 when a post failed (its `reason=` is on the line), 2 for bad input: invalid JSON, a page URL that is not an absolute http(s) URL without a fragment, an unreadable plan or ledger.
