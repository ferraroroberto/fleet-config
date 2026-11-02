"""The web-page leg of /design-review: a static site measured against its own preview (design.web.md).

`preview` starts and stops the site's preview server, `page_walk` (run under
the target's interpreter) records raw facts at 390, 800 and 1440 px, `seo`
reads the search facts over HTTP, `checks` holds the pass rules, and
`measure_web` assembles a `metrics.json` that `evaluate` and `report` score
and render with `design.web.rubric.toml`.
"""
