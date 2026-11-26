"""The proposal page's theme tokens, stylesheet and script, inlined so the page fetches nothing.

Token values are the fleet design system's (`~/.claude/design.md` light,
`~/.claude/design.dark.md` dark); the names match so `/design-sync` reads them.
"""

from __future__ import annotations

from typing import Dict

LIGHT: Dict[str, str] = {
    "canvas": "#ffffff", "canvas-subtle": "#f6f8fa", "card": "#ffffff",
    "border": "#d1d9e0", "border-muted": "#d8dee4", "fg": "#1f2328", "fg-muted": "#656d76",
    "accent": "#0969da", "accent-fg": "#ffffff", "accent-fill": "#0969da", "accent-text": "#0550ae",
    "success-text": "#116329", "attention-text": "#7d4e00", "danger-text": "#a40e26",
    "control-border": "#818b98", "control-fill": "#f6f8fa",
}
DARK: Dict[str, str] = {
    "canvas": "#0d1117", "canvas-subtle": "#010409", "card": "#161b22",
    "border": "#30363d", "border-muted": "#21262d", "fg": "#e6edf3", "fg-muted": "#7d8590",
    "accent": "#2f81f7", "accent-fg": "#ffffff", "accent-fill": "#1f6feb", "accent-text": "#58a6ff",
    "success-text": "#56d364", "attention-text": "#e3b341", "danger-text": "#ff7b72",
    "control-border": "#6e7681", "control-fill": "#21262d",
}


def _vars(tokens: Dict[str, str]) -> str:
    return "".join(f"--{name}:{value};" for name, value in tokens.items())


def theme_css() -> str:
    """Light by default, dark from the OS preference, and either one forced by the toggle."""
    light, dark = _vars(LIGHT), _vars(DARK)
    return (f":root{{color-scheme:light;{light}}}"
            f"@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{color-scheme:dark;{dark}}}}}"
            f":root[data-theme=dark]{{color-scheme:dark;{dark}}}"
            f":root[data-theme=light]{{color-scheme:light;{light}}}")


STYLE = """
:root{--accent-soft:color-mix(in srgb,var(--accent) 16%,transparent);
--accent-border-soft:color-mix(in srgb,var(--accent) 24%,transparent);
--neutral-soft:color-mix(in srgb,var(--fg-muted) 16%,transparent);
--success-soft:color-mix(in srgb,var(--success-text) 16%,transparent);
--attention-soft:color-mix(in srgb,var(--attention-text) 16%,transparent);
--danger-soft:color-mix(in srgb,var(--danger-text) 16%,transparent)}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--fg);font:400 1rem/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:772px;margin:0 auto;padding:0 12px 32px}
header.head{display:flex;align-items:center;gap:8px;min-height:52px;padding:8px 2px}
header.head div{flex:1;min-width:0}
h1{font-size:1.25rem;font-weight:700;line-height:1.25;margin:0}
.ctx{font-size:.875rem;color:var(--fg-muted);margin:2px 0 0}
.icon-button{width:44px;height:44px;border:0;background:transparent;color:var(--fg-muted);border-radius:12px;cursor:pointer;display:grid;place-items:center}
.icon-button:hover{color:var(--fg)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px;margin:12px 0}
h2{font-size:1rem;font-weight:700;margin:0 0 8px}
.chart svg{display:block;width:100%;height:auto;max-width:520px;margin:0 auto}
.chart .grid{stroke:var(--border-muted);stroke-width:1}
.chart .mid{stroke:var(--control-border);stroke-width:1;stroke-dasharray:4 4}
.chart .qw{fill:var(--accent-soft)}
.chart .axis{fill:var(--fg-muted);font-size:11px;font-weight:600}
.chart .qlabel{fill:var(--fg-muted);font-size:11px;font-weight:600}
.chart .qlabel.win{fill:var(--accent-text)}
.dot circle{fill:var(--accent-fill);stroke:var(--card);stroke-width:2}
.dot text{fill:var(--accent-fg);font-size:10px;font-weight:700;pointer-events:none}
.dot[data-quadrant=money-pit] circle,.dot[data-quadrant=fill-in] circle{fill:var(--fg-muted)}
.dot:hover circle,.dot:focus-visible circle{stroke:var(--fg)}
.filters{display:grid;gap:8px}
.fgroup{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.fgroup .flabel{font-size:.875rem;font-weight:600;color:var(--fg-muted);min-width:72px}
.fchip{min-height:32px;padding:4px 12px;border-radius:9999px;border:1px solid transparent;background:var(--neutral-soft);color:var(--fg);font:600 .75rem/1.1 system-ui,sans-serif;cursor:pointer;position:relative}
.fchip::after{content:"";position:absolute;inset:-6px 0}
.fchip[aria-pressed=true]{background:var(--accent-soft);color:var(--accent-text);border-color:var(--accent-border-soft)}
.toolbar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin-top:4px}
.count{font-size:.875rem;color:var(--fg-muted)}
select{height:36px;border-radius:12px;border:1px solid var(--control-border);background:var(--control-fill);color:var(--fg);font:600 .875rem system-ui,sans-serif;padding:0 8px}
.reset{height:36px;border-radius:12px;border:1px solid var(--border);background:transparent;color:var(--fg-muted);font:600 .875rem system-ui,sans-serif;padding:0 12px;cursor:pointer}
ol.items{list-style:none;margin:0;padding:0}
.item{border-top:1px solid var(--border-muted);padding:12px 0;scroll-margin-top:12px}
.item:first-child{border-top:0;padding-top:0}
.item:target{background:var(--accent-soft);border-radius:12px;padding:12px;margin:0 -12px}
.item h3{display:flex;gap:8px;align-items:baseline;font-size:1rem;font-weight:600;margin:0}
.num{flex:none;min-width:24px;height:24px;border-radius:9999px;background:var(--accent-soft);color:var(--accent-text);font:700 .75rem/24px system-ui,sans-serif;text-align:center}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.chip{padding:2px 8px;border-radius:9999px;background:var(--neutral-soft);color:var(--fg);font:600 .75rem/1.4 system-ui,sans-serif}
.chip.quick-win{background:var(--success-soft);color:var(--success-text)}
.chip.big-bet{background:var(--accent-soft);color:var(--accent-text)}
.chip.money-pit{background:var(--danger-soft);color:var(--danger-text)}
.chip.fill-in{background:var(--attention-soft);color:var(--attention-text)}
.item p{margin:4px 0;font-size:.875rem}
.item .muted{color:var(--fg-muted)}
.empty{color:var(--fg-muted);text-align:center;padding:32px 16px;margin:0}
[hidden]{display:none!important}
"""

SCRIPT = """
(function(){
var root=document.documentElement,groups=["repo","category","quadrant"],on={};
groups.forEach(function(g){on[g]=new Set();});
var rows=[].slice.call(document.querySelectorAll(".item")),dots=[].slice.call(document.querySelectorAll(".dot"));
var list=document.querySelector("ol.items"),count=document.getElementById("count"),empty=document.getElementById("empty");
function keep(el){return groups.every(function(g){return !on[g].size||on[g].has(el.dataset[g]);});}
function apply(){var shown=0;
rows.forEach(function(r){var k=keep(r);r.hidden=!k;if(k)shown++;});
dots.forEach(function(d){d.style.display=keep(d)?"":"none";});
count.textContent="Showing "+shown+" of "+rows.length;empty.hidden=shown>0;}
document.querySelectorAll(".fchip").forEach(function(b){b.addEventListener("click",function(){
var s=on[b.dataset.group],v=b.dataset.value,p=!s.has(v);if(p)s.add(v);else s.delete(v);
b.setAttribute("aria-pressed",String(p));apply();});});
document.getElementById("reset").addEventListener("click",function(){
groups.forEach(function(g){on[g].clear();});
document.querySelectorAll(".fchip").forEach(function(b){b.setAttribute("aria-pressed","false");});apply();});
var keys={impact:function(r){return [-r.dataset.impact,+r.dataset.effort,+r.dataset.order];},
effort:function(r){return [+r.dataset.effort,-r.dataset.impact,+r.dataset.order];},
ratio:function(r){return [-(r.dataset.impact/r.dataset.effort),+r.dataset.order];},
order:function(r){return [+r.dataset.order];}};
document.getElementById("sort").addEventListener("change",function(e){var k=keys[e.target.value];
rows.slice().sort(function(a,b){var x=k(a),y=k(b);for(var i=0;i<x.length;i++){if(x[i]!==y[i])return x[i]-y[i];}return 0;})
.forEach(function(r){list.appendChild(r);});});
document.getElementById("theme").addEventListener("click",function(){
var dark=root.dataset.theme?root.dataset.theme==="dark":matchMedia("(prefers-color-scheme: dark)").matches;
root.dataset.theme=dark?"light":"dark";});
apply();})();
"""
