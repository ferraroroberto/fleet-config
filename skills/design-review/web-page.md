# design-review — web-page mode

A **web page** (a public static site, `SPEC_KIND=web-page`) has no running app to probe. It is held to `design.web.md` and scored with `design.web.rubric.toml`, never the PWA spec. This mode replaces steps 1 and 2 of the main flow; steps 3 to 7 then run on the run directory it prints, unchanged.

## 1. Measure against the site's own preview

```
... design_review measure-web <repo> [--pages /,/about | all] [--devices phone,tablet,desktop] [--no-perf]
```

It resolves the preview from the target repo (`.fleet.toml` `[design.review.preview]` when declared, else `scripts/site_serve.py`, else `scripts/serve.py`), starts it under the target's `.venv` on a free loopback port, measures, and stops **that PID only**. It never touches a server already running. Pages default to the `key views` of the repo's `## UX surface` block; `all` takes every sitemap URL.

Per page and viewport (390 px phone at scale 2 with touch, 800 px tablet, 1440 px desktop) it scrolls once so lazy images load, takes a full-page screenshot and measures the page. Then it runs the interactive checks: the phone-menu hit test (`elementFromPoint` at each link's centre), one click on each carousel arrow, the sticky-media scan (1440) and the keyboard focus walk. A cold phone-profile load under Lighthouse's mobile throttling gives the Web Vitals, bytes and requests. The search facts (title, description, canonical, social card, JSON-LD, sitemap, `robots.txt`, `_redirects`, the 404 status, broken internal links) are read over HTTP from the preview.

Read these lines:

- `SCREENS=<ok>/<total>` and `UNMEASURED=`: `NO_PREVIEW`, `BAD_PREVIEW`, `PREVIEW_FAILED`, `PLAYWRIGHT_MISSING` or `WALK_FAILED` end the run with that reason; print it.
- `SPEC_USER_SCOPE=`: `not_confirmed` means `~/.claude/design.web.md` is not linked on this machine (an elevated `install.ps1` run fixes it; never create the link from here). The review reads this repo's `design.web.md` regardless; print the state on its own line, never as a pass.
- `PREVIEW=<source> PID=<pid> (stopped)`: the preview the run started and stopped.

## 2. Then steps 3 to 7

`evaluate`, `judge-prompt`, `judge-merge`, `ledger` and `render` read `surface = "web-page"` from `metrics.json` and pick `design.web.rubric.toml` and `design.web.md` without flags. The Lighthouse rule (`WEB-PERF-04`) stays `unmeasured` because this review does not run the Lighthouse CLI. Report it as unmeasured, never as a pass.

## Pass rules (`skills/_lib/design_review/web/checks.py`)

- **Phone reading order:** in each row on a phone, a picture read before a text block fails, unless the desktop shows the picture first (above the text, or beside it on the left).
- **Image roles:** images of one role render within 2 px of each other. Beside a picture, a text column gets at least a third of the row (the 152 px column at 800 px fails).
- **Whole-card carousel:** at 1440, 3 whole cards and 0 partial ones when the row scrolls. On a phone, 1 whole card plus the next card's edge. One click moves one card plus the gap, and the arrows sit centred (2 px).
- **Sticky media:** no blank stretch over 200 px beside the text.
- **Portraits:** one size and radius, top level with the name (4 px).
- **Footer band:** body text 7:1, label heads 4.5:1.
- **First-load weight:** within the page type's budget from `design.web.md`. The page type comes from `[design.review.page_types]` globs in `.fleet.toml`, else the home page, else the page's JSON-LD (`Product`, `Article`, `VideoObject`), else `landing`.
