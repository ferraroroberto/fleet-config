"""The stylesheet of the `/context-purge` digest page, kept apart from the renderer in `digest.py`."""

# Light and dark tokens, both themes reachable by `data-theme`, so the page reads in either.
PAGE_CSS = """:root { --bg:#fbfbfa; --fg:#1f2328; --muted:#6b7280; --line:#e5e7eb;
        --card:#ffffff; --accent:#b45309; --warn:#fef3c7; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#16181d; --fg:#e6e8eb; --muted:#9ca3af; --line:#2b2f36;
  --card:#1c1f26; --accent:#fbbf24; --warn:#3a2f10; } }
:root[data-theme="dark"] { --bg:#16181d; --fg:#e6e8eb; --muted:#9ca3af;
  --line:#2b2f36; --card:#1c1f26; --accent:#fbbf24; --warn:#3a2f10; }
body { background:var(--bg); color:var(--fg); margin:0; padding:1.5rem 1rem 4rem;
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
main { max-width:60rem; margin:0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
h2 { font-size:1.05rem; margin:2rem 0 .5rem; padding-bottom:.3rem;
  border-bottom:1px solid var(--line); }
.sub { color:var(--muted); margin:0 0 1.5rem; }
.grid { display:grid; gap:.75rem; grid-template-columns:repeat(auto-fit,minmax(11rem,1fr)); }
.stat { background:var(--card); border:1px solid var(--line); border-radius:.6rem; padding:.8rem; }
.stat .n { font-size:1.5rem; font-weight:600; }
.stat .l { color:var(--muted); font-size:.82rem; }
.banner { background:var(--warn); border:1px solid var(--accent); border-radius:.6rem;
  padding:.8rem; margin-bottom:1.25rem; }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:.88rem; }
th,td { text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line);
  vertical-align:top; }
th { color:var(--muted); font-weight:600; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85em; }
.was { color:var(--muted); text-decoration:line-through; }
.now { margin-top:.3rem; }
ul { padding-left:1.1rem; }
"""
