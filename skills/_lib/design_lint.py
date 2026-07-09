"""Deterministic design-system lint for /design-sync v2 (fleet-config#277).

Pure logic + a small CLI, same `_lib` contract as `cert_drift.py` /
`ux_surface.py`: everything mechanically checkable about a web app's
conformance to the fleet design system lives HERE, not in LLM judgment. The
skill orchestrates; this module measures.

Subcommands (all print JSON):

  tokens    <root>   spec-token → app-var mapping via the built-in alias
                     table, with per-theme drift/missing/unmapped. The LLM
                     resolves only the `unmapped` leftovers.
  adoption  <root>   per-family tokenized/total declaration ratios
                     (color, font-size, radius, spacing) + escapees.
  contracts <root>   greppable design.md v2 component-contract checks
                     (focus ring, reduced motion, desktop measure, switch
                     on-color, native checkboxes, disclosure box, native
                     <dialog>, nav rules, icon-size strays).
  vendored  <root>   byte-compare the app's _vendored/ copies against
                     project-scaffolding's canonical files.
  siblings  <root>   same-name top-level JS definitions across >=2 files
                     (the 7x-duplicated `schedule(ms)` case).
  all       <root>   every section in one JSON document.

Spec files are read from `~/.claude/design.md` + `design.dark.md` (junctioned
there by install.ps1); override with --spec/--spec-dark for tests. The
scaffold root for `vendored` defaults to E:/automation/project-scaffolding;
override with --scaffold.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------- files

SKIP_DIR_PARTS = {".git", ".venv", "node_modules", "__pycache__", "spike", "spikes"}


def repo_files(root: Path, suffixes: Tuple[str, ...]) -> List[Path]:
    """Tracked files by suffix — `git ls-files` when available, rglob fallback."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15,
        )
        if out.returncode == 0:
            names = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
            paths = [root / n for n in names]
        else:
            raise OSError(out.stderr.strip() or "git ls-files failed")
    except (OSError, subprocess.SubprocessError):
        paths = list(root.rglob("*"))
    keep: List[Path] = []
    for p in paths:
        if p.suffix.lower() not in suffixes or not p.is_file():
            continue
        if any(part in SKIP_DIR_PARTS for part in p.parts):
            continue
        keep.append(p)
    return sorted(keep)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def find_vendored_root(root: Path) -> Optional[Path]:
    """Locate the app-side `static/_vendored` dir regardless of layout.

    project-scaffolding's own `app/webapp/static/` is one layout among several
    the fleet actually uses (`app/static/` — grocery; `app_web/static/` —
    local-llm-hub), so this searches rather than hardcoding the scaffold's
    path (fleet-config#291, #292). Bounded to two path segments ahead of
    `static/_vendored` — deeper nesting isn't a layout seen in the fleet.
    """
    candidates = sorted(p for p in root.glob("*/static/_vendored") if p.is_dir())
    candidates += sorted(p for p in root.glob("*/*/static/_vendored") if p.is_dir())
    for c in candidates:
        if not any(part in SKIP_DIR_PARTS for part in c.parts):
            return c
    return None


# ------------------------------------------------------------- spec parsing

_INLINE_MAP_RE = re.compile(r"^\{(.*)\}$", re.S)


def _strip_comment(value: str) -> str:
    """Drop a trailing `# comment` that is outside any quotes."""
    out: List[str] = []
    quote: Optional[str] = None
    for ch in value:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).strip()


def _clean_value(value: str) -> str:
    v = _strip_comment(value).strip().rstrip(",")
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    return v.strip()


def _split_inline_map(body: str) -> List[str]:
    """Split `k: v, k2: v2` on commas outside quotes/parens."""
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    quote: Optional[str] = None
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_spec(text: str) -> Dict[str, str]:
    """Flatten a design.md frontmatter into {'colors.canvas': '#ffffff', ...}.

    Tolerant, indent-tracked, line-based parser for the fleet's known format
    (no YAML dependency): flat leaves, nested groups (icons.size), and inline
    `{ k: v, ... }` maps. `{path.to.token}` references resolve afterwards.
    """
    lines = text.splitlines()
    # isolate the frontmatter block
    try:
        start = lines.index("---") + 1
        end = lines.index("---", start)
    except ValueError:
        start, end = 0, len(lines)

    flat: Dict[str, str] = {}
    stack: List[Tuple[int, str]] = []  # (indent, key)
    i = start
    while i < end:
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", stripped)
        if not m:
            continue  # continuation lines (folded description text)
        key, rest = m.group(1), m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        rest_clean = _strip_comment(rest).strip()
        if not rest_clean:
            stack.append((indent, key))  # group opener
            continue
        if rest_clean == ">":
            continue  # folded scalar (description) — skip its block
        im = _INLINE_MAP_RE.match(rest_clean)
        if im:
            for item in _split_inline_map(im.group(1)):
                km = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", item)
                if km:
                    flat[f"{path}.{km.group(1)}"] = _clean_value(km.group(2))
            continue
        flat[path] = _clean_value(rest)

    # resolve {path.to.token} references (bounded)
    for _ in range(4):
        changed = False
        for k, v in list(flat.items()):
            refs = re.findall(r"\{([A-Za-z0-9_.-]+)\}", v)
            for r in refs:
                if r in flat and "{" not in flat[r]:
                    v = v.replace("{" + r + "}", flat[r])
                    changed = True
            flat[k] = v
        if not changed:
            break
    return flat


# ------------------------------------------------------- CSS custom props

def strip_comments(text: str, kind: str) -> str:
    """Blank out comments, preserving newlines so file:line stays correct.

    Without this, a `/* ... @media (color-gamut: p3) ... */` prose comment
    makes the media scanner swallow the following `:root` block (the bug this
    fixed against home-automation's real stylesheet).
    """
    def nl(m: "re.Match[str]") -> str:
        return "\n" * m.group(0).count("\n")

    if kind == "css":
        return re.sub(r"/\*.*?\*/", nl, text, flags=re.S)
    if kind == "js":
        return re.sub(r"/\*.*?\*/", nl, text, flags=re.S)
    if kind == "html":
        return re.sub(r"<!--.*?-->", nl, text, flags=re.S)
    return text


_BLOCK_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);")

DARK_SELECTOR_HINTS = ('[data-theme="dark"]', "[data-theme='dark']", ".theme-dark", ".dark")


def _flatten_media(css: str) -> List[Tuple[str, str]]:
    """Yield (media_condition, inner_css) with '' for top level.

    One nesting level of @media is enough for the fleet's stylesheets.
    """
    out: List[Tuple[str, str]] = []
    i = 0
    while i < len(css):
        m = re.search(r"@media\s*([^{]+)\{", css[i:])
        if not m:
            out.append(("", css[i:]))
            break
        out.append(("", css[i:i + m.start()]))
        # find the matching close brace for the media block
        depth = 1
        j = i + m.end()
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        out.append((m.group(1).strip(), css[i + m.end():j - 1]))
        i = j
    return out


def parse_custom_props(css: str, fname: str) -> Dict[str, Dict[str, Tuple[str, int]]]:
    """Extract custom-property definitions per theme.

    Returns {'light': {name: (value, line)}, 'dark': {...}}. P3 wide-gamut
    `@media (color-gamut: p3)` twins are excluded (they would clobber the sRGB
    values the spec comparison targets).
    """
    css = strip_comments(css, "css")
    themes: Dict[str, Dict[str, Tuple[str, int]]] = {"light": {}, "dark": {}}
    for media, chunk_css in _flatten_media(css):
        if "color-gamut" in media:
            continue
        media_dark = "prefers-color-scheme" in media and "dark" in media
        for bm in _BLOCK_RE.finditer(chunk_css):
            selector = bm.group(1).strip().splitlines()[-1].strip()
            body = bm.group(2)
            sel_dark = any(h in selector for h in DARK_SELECTOR_HINTS)
            is_root = ":root" in selector or selector == "html"
            if not (is_root or sel_dark):
                continue
            theme = "dark" if (sel_dark or media_dark) else "light"
            # line numbers: count newlines up to the declaration
            base = css.find(body)
            for dm in _DECL_RE.finditer(body):
                line = css[: base + dm.start()].count("\n") + 1 if base >= 0 else 0
                themes[theme][dm.group(1)] = (dm.group(2).strip(), line)
    return themes


def normalize_value(v: str) -> str:
    v = v.strip().lower()
    v = re.sub(r"\s+", " ", v)
    m = re.fullmatch(r"#([0-9a-f]{3})", v)
    if m:
        v = "#" + "".join(ch * 2 for ch in m.group(1))
    return v


# ----------------------------------------------------------- token mapping

# spec role → candidate app custom-property names (without the -- prefix).
ALIASES: Dict[str, List[str]] = {
    "colors.canvas": ["bg", "canvas", "background"],
    "colors.canvas-subtle": ["card-off", "canvas-subtle", "bg-subtle", "surface-subtle"],
    "colors.card": ["card", "surface"],
    "colors.border": ["line", "border"],
    "colors.border-muted": ["line-muted", "border-muted"],
    "colors.fg": ["ink", "fg", "text"],
    "colors.fg-muted": ["muted", "fg-muted", "text-muted"],
    "colors.accent": ["accent", "link", "primary"],
    "colors.accent-fg": ["accent-fg", "on-accent"],
    "colors.accent-soft": ["accent-soft"],
    "colors.accent-border-soft": ["accent-border-soft"],
    "colors.accent-border-strong": ["accent-border-strong"],
    "colors.success": ["on", "success", "ok"],
    "colors.danger": ["deficit", "danger", "error"],
    "colors.attention": ["attention", "warning"],
    "colors.tile-green": ["tile-green"],
    "colors.tile-blue": ["tile-blue"],
    "colors.tile-purple": ["tile-purple"],
    "colors.tile-orange": ["tile-orange"],
    "colors.tile-yellow": ["tile-yellow"],
    "rounded.sm": ["radius-sm"],
    "rounded.md": ["radius-md"],
    "rounded.lg": ["radius", "radius-lg"],
    "rounded.pill": ["radius-pill"],
    "rounded.nav": ["radius-nav"],
    "spacing.xs": ["space-xs"],
    "spacing.sm": ["space-sm"],
    "spacing.md": ["space-md"],
    "spacing.lg": ["space-lg"],
    "spacing.xl": ["space-xl"],
    "spacing.gutter": ["gap", "gutter"],
    "typography.heading-xl.fontSize": ["font-heading-xl"],
    "typography.heading-lg.fontSize": ["font-heading-lg"],
    "typography.body.fontSize": ["font-body"],
    "typography.label.fontSize": ["font-label"],
    "typography.caption.fontSize": ["font-caption"],
    "components.control.height": ["control-h"],
    "icons.size.inline": ["icon-inline"],
    "icons.size.title": ["icon-title"],
    "icons.size.feature": ["icon-feature"],
}

# tokens whose absence is *not* a finding (optional adoptions)
OPTIONAL_ROLES = {
    "rounded.sm",  # home-automation deliberately dropped it (#361)
    "icons.size.inline", "icons.size.title", "icons.size.feature",
    "colors.tile-green", "colors.tile-blue", "colors.tile-purple",
    "colors.tile-orange", "colors.tile-yellow",
    # accent derivatives — only apps that ship a tint tier define them (#296)
    "colors.accent-soft", "colors.accent-border-soft",
    "colors.accent-border-strong",
}


def map_tokens(
    spec_light: Dict[str, str],
    spec_dark: Dict[str, str],
    app: Dict[str, Dict[str, Tuple[str, int]]],
    fname: str,
) -> Dict[str, object]:
    matches: List[dict] = []
    drift: List[dict] = []
    missing: List[dict] = []
    claimed: set = set()
    for role, candidates in ALIASES.items():
        spec_vals = {"light": spec_light.get(role), "dark": spec_dark.get(role)}
        if spec_vals["light"] is None and spec_vals["dark"] is None:
            continue
        var = None
        for cand in candidates:
            if f"--{cand}" in app["light"] or f"--{cand}" in app["dark"]:
                var = f"--{cand}"
                break
        if var is None:
            if role not in OPTIONAL_ROLES:
                missing.append({"role": role, "candidates": candidates})
            continue
        claimed.add(var)
        for theme in ("light", "dark"):
            spec_v = spec_vals[theme]
            if spec_v is None:
                continue
            got = app[theme].get(var)
            if got is None:
                # dark themes inherit theme-invariant structural tokens from
                # :root (radii, spacing) — but a token whose spec value
                # DIFFERS per theme (colors) genuinely needs the dark
                # override, so its absence is a finding.
                if (theme == "dark" and var in app["light"]
                        and normalize_value(spec_vals["light"] or "")
                        == normalize_value(spec_vals["dark"] or "")):
                    continue
                drift.append({"role": role, "var": var, "theme": theme,
                              "app": None, "spec": spec_v,
                              "file": fname, "line": 0, "kind": "missing-theme-value"})
                continue
            app_v, line = got
            if normalize_value(app_v) != normalize_value(spec_v):
                drift.append({"role": role, "var": var, "theme": theme,
                              "app": app_v, "spec": spec_v,
                              "file": fname, "line": line, "kind": "value-drift"})
            else:
                matches.append({"role": role, "var": var, "theme": theme})
    unmapped = sorted(
        v for v in set(app["light"]) | set(app["dark"]) if v not in claimed
    )
    return {"matched": matches, "drift": drift, "missing": missing,
            "unmapped": unmapped}


# --------------------------------------------------------------- adoption

_COLOR_LITERAL_RE = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\boklch\(|\boklab\(")
_COLOR_PROPS = ("color", "background", "background-color", "border",
                "border-color", "border-top", "border-bottom", "border-left",
                "border-right", "outline", "fill", "stroke")
_SPACING_PROPS = ("padding", "margin", "gap", "row-gap", "column-gap",
                  "padding-top", "padding-bottom", "padding-left", "padding-right",
                  "padding-inline", "padding-block",
                  "margin-top", "margin-bottom", "margin-left", "margin-right",
                  "margin-inline", "margin-block")

_ANY_DECL_RE = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;{}]+);")


def _family_of(prop: str, value: str) -> Optional[str]:
    p = prop.lower()
    if p == "font-size":
        return "font-size"
    if p == "border-radius":
        return "radius"
    if p in _SPACING_PROPS:
        return "spacing"
    if p in _COLOR_PROPS:
        # only count declarations that actually carry a color
        if "var(" in value or _COLOR_LITERAL_RE.search(value):
            return "color"
        return None
    return None


_SPACING_EXEMPT = re.compile(r"^(0|auto|inherit|initial|unset|none)( (0|auto))*$")
_RADIUS_EXEMPT = re.compile(r"^(0|50%|inherit)$")


def adoption(root: Path, css_files: List[Path]) -> Dict[str, object]:
    fam: Dict[str, Dict[str, object]] = {
        f: {"tokenized": 0, "total": 0, "escapees": [], "literals": {}}
        for f in ("color", "font-size", "radius", "spacing")
    }
    for path in css_files:
        css = strip_comments(read_text(path), "css")
        for m in _ANY_DECL_RE.finditer(css):
            prop, value = m.group(1), m.group(2).strip()
            if prop.startswith("--"):
                continue
            family = _family_of(prop, value)
            if family is None:
                continue
            v = value.strip()
            if family == "spacing" and _SPACING_EXEMPT.fullmatch(v):
                continue
            if family == "radius" and _RADIUS_EXEMPT.fullmatch(v):
                continue
            if family == "color" and v.lower() in ("transparent", "currentcolor", "inherit", "none"):
                continue
            entry = fam[family]
            entry["total"] += 1
            if "var(--" in v:
                entry["tokenized"] += 1
            else:
                line = css[: m.start()].count("\n") + 1
                lits: Dict[str, int] = entry["literals"]  # type: ignore[assignment]
                lits[v] = lits.get(v, 0) + 1
                esc: List[dict] = entry["escapees"]  # type: ignore[assignment]
                if len(esc) < 40:
                    esc.append({"file": rel(root, path), "line": line,
                                "prop": prop, "value": v})
    for family, entry in fam.items():
        total = int(entry["total"])  # type: ignore[arg-type]
        tok = int(entry["tokenized"])  # type: ignore[arg-type]
        entry["ratio"] = round(tok / total, 3) if total else None
        entry["literals"] = dict(sorted(
            entry["literals"].items(), key=lambda kv: -kv[1])[:15])  # type: ignore[union-attr]
    return fam


# --------------------------------------------------------------- contracts

def _find_line(text: str, pattern: str, flags: int = 0) -> Optional[int]:
    m = re.search(pattern, text, flags)
    return text[: m.start()].count("\n") + 1 if m else None


_STANDALONE_MEDIA_RE = re.compile(r"@media[^{]*display-mode:\s*standalone[^{]*\{")


def standalone_shell_present(css: str) -> bool:
    """True when a `(display-mode: standalone)` media block makes the app shell
    the scroller: position: fixed + 100lvh sizing + overflow-y: auto — the
    home-automation#303 architecture the design.md nav contract requires (all
    real scrolling inside `.app`, never the window; removes the pill-drift
    cause). Works on comment-stripped CSS; nesting inside another @media is
    fine (the inner standalone block is matched directly)."""
    for m in _STANDALONE_MEDIA_RE.finditer(css):
        depth, i = 1, m.end()
        start = i
        while i < len(css) and depth:
            if css[i] == "{":
                depth += 1
            elif css[i] == "}":
                depth -= 1
            i += 1
        block = css[start:i]
        if (re.search(r"position:\s*fixed", block) and "100lvh" in block
                and re.search(r"overflow-y:\s*auto", block)):
            return True
    return False


_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
              "link", "meta", "param", "source", "track", "wbr"}
_TAG_STREAM_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>")
_TAG_CLASS_RE = re.compile(r"class=[\"']([^\"']*)[\"']", re.I)


def nav_nested_in_app(html_files: List[Path]) -> bool:
    """True when a `<nav class="tabs">` is a DOM descendant of `<main
    class="app">` — the _vendored/nav/README.md load-bearing rule is that
    they must be `<body>` siblings; an installed iOS PWA can capture a fixed
    nav inside a scroll container and anchor it there instead of the
    viewport (home-automation#232, app-launcher#369)."""
    for p in html_files:
        text = strip_comments(read_text(p), "html")
        stack: List[Tuple[str, set]] = []
        for m in _TAG_STREAM_RE.finditer(text):
            closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
            if closing:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == tag:
                        del stack[i:]
                        break
                continue
            cls_m = _TAG_CLASS_RE.search(attrs)
            classes = set(cls_m.group(1).split()) if cls_m else set()
            if tag == "nav" and "tabs" in classes:
                if any(t == "main" and "app" in c for t, c in stack):
                    return True
            self_closing = attrs.rstrip().endswith("/") or tag in _VOID_TAGS
            if not self_closing:
                stack.append((tag, classes))
    return False


_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # mahjong/cards, symbols & pictographs, transport, supplemental, emoticons
    "\U0001F1E6-\U0001F1FF"   # regional indicators (flags)
    "⌀-⏿"           # misc technical (e.g. the branch glyph U+2387)
    "☀-➿"           # misc symbols + dingbats (e.g. U+2605 star, U+2600 sun)
    "⬀-⯿"           # misc symbols and arrows
    "]"
)
_TAG_RE = re.compile(r"<[^>]*>")


def find_emoji_sites(root: Path, html_files: List[Path], js_files: List[Path]
                      ) -> List[str]:
    """`file:line` for emoji glyphs in rendered text — HTML text nodes (tags
    stripped so attribute values don't count) and JS source (string literals
    used as UI copy, e.g. an empty-state message baked into a .js file, are
    indistinguishable from other JS text at grep level, so the whole file is
    scanned — app-launcher#368)."""
    sites: List[str] = []
    for p in html_files:
        text = strip_comments(read_text(p), "html")
        text_nodes = _TAG_RE.sub(" ", text)
        for m in _EMOJI_RE.finditer(text_nodes):
            line = text_nodes.count("\n", 0, m.start()) + 1
            sites.append(f"{rel(root, p)}:{line}")
    for p in js_files:
        text = strip_comments(read_text(p), "js")
        for m in _EMOJI_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            sites.append(f"{rel(root, p)}:{line}")
    return sites


# ------------------------------------------------------------ editor modal
#
# A native <dialog> containing a <form> with real inputs is an "editor
# modal" and held to design.md's `modal` component contract (fleet-config#307
# — the app-launcher#70 root cause: `.stacked` was styled only inside
# `.settings-card`, so the same class fell back to unstyled inline labels
# inside <dialog> forms, and raw <fieldset> groups had no CSS at all).

_DIALOG_RE = re.compile(r"<dialog\b([^>]*)>(.*?)</dialog>", re.S | re.I)
_LABEL_CLASS_RE = re.compile(r"<label\b[^>]*\bclass=[\"']([^\"']*)[\"']", re.I)
_FIELDSET_OPEN_RE = re.compile(r"<fieldset\b([^>]*)>", re.I)
_BUTTON_RE = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.S | re.I)
_FOOTER_CONTAINER_RE = re.compile(r"<(div|footer)\b([^>]*)>(.*?)</\1>", re.S | re.I)
_FIELDSET_TAG_PAT = re.compile(r"^fieldset(?:[.:#\[]|$)", re.I)


def _editor_modals(root: Path, html_files: List[Path]) -> List[dict]:
    """`<dialog>` blocks that contain a `<form>` with a real input/select/
    textarea — i.e. detail/rename/settings editors, not a plain alert or
    results dialog."""
    modals: List[dict] = []
    for p in html_files:
        text = strip_comments(read_text(p), "html")
        for m in _DIALOG_RE.finditer(text):
            attrs, inner = m.group(1), m.group(2)
            if not (re.search(r"<form\b", inner, re.I)
                    and re.search(r"<input\b|<select\b|<textarea\b", inner, re.I)):
                continue
            cls_m = _TAG_CLASS_RE.search(attrs)
            classes = set(cls_m.group(1).split()) if cls_m else set()
            line = text.count("\n", 0, m.start()) + 1
            modals.append({"file": rel(root, p), "line": line, "classes": classes,
                           "inner": inner, "text": text, "inner_start": m.start(2)})
    return modals


def _loc_in_modal(modal: dict, local_pos: int) -> str:
    abs_pos = modal["inner_start"] + local_pos
    line = modal["text"].count("\n", 0, abs_pos) + 1
    return f"{modal['file']}:{line}"


def _last_selector_line(sel_group: str) -> str:
    lines = sel_group.strip().splitlines()
    return lines[-1].strip() if lines else ""


def _split_top_level_commas(sel_line: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in sel_line:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _compounds(selector: str) -> List[str]:
    return [c for c in re.split(r"\s*[>+~]\s*|\s+", selector.strip()) if c]


def _selector_hits(css_all: str, rightmost_pattern: "re.Pattern[str]"
                    ) -> List[Tuple[str, List[str], str]]:
    """`(selector, ancestor_compounds, declaration_body)` for every selector
    whose rightmost (target) compound matches `rightmost_pattern`.
    `::backdrop` pseudo-elements are excluded — they never carry the layout
    declarations these checks look for."""
    hits: List[Tuple[str, List[str], str]] = []
    for bm in _BLOCK_RE.finditer(css_all):
        sel_line = _last_selector_line(bm.group(1))
        if not sel_line or sel_line.startswith("@") or sel_line.startswith("*/"):
            continue
        for sel in _split_top_level_commas(sel_line):
            if "::backdrop" in sel:
                continue
            comps = _compounds(sel)
            if comps and rightmost_pattern.search(comps[-1]):
                hits.append((sel, comps[:-1], bm.group(2)))
    return hits


def _class_scope_status(css_all: str, class_name: str) -> set:
    """Where a class is styled: `global` (bare/unscoped), `dialog` (an
    ancestor compound mentions "dialog"), `other` (scoped to some unrelated
    ancestor, e.g. `.settings-card .stacked` — the app-launcher#70 bug)."""
    pat = re.compile(r"\." + re.escape(class_name) + r"\b")
    scopes: set = set()
    for _, ancestors, _body in _selector_hits(css_all, pat):
        if not ancestors:
            scopes.add("global")
        elif any("dialog" in a.lower() for a in ancestors):
            scopes.add("dialog")
        else:
            scopes.add("other")
    return scopes


def contracts(
    root: Path,
    css_files: List[Path],
    html_files: List[Path],
    js_files: List[Path],
    spec_light: Dict[str, str],
    spec_dark: Optional[Dict[str, str]] = None,
) -> List[dict]:
    css_all = "\n".join(f"/*FILE {rel(root, p)}*/\n" + strip_comments(read_text(p), "css")
                        for p in css_files)
    markup_all = "\n".join(
        f"/*FILE {rel(root, p)}*/\n"
        + strip_comments(read_text(p), "html" if p.suffix == ".html" else "js")
        for p in html_files + js_files)

    def evidence(blob: str, pattern: str, flags: int = 0) -> Optional[str]:
        m = re.search(pattern, blob, flags)
        if not m:
            return None
        fh = blob.rfind("/*FILE ", 0, m.start())
        fname = blob[fh + 7: blob.find("*/", fh)] if fh >= 0 else "?"
        line = blob.count("\n", blob.find("*/", fh) + 2 if fh >= 0 else 0, m.start()) + 1
        return f"{fname}:{line}"

    def loc_at(blob: str, pos: int) -> str:
        fh = blob.rfind("/*FILE ", 0, pos)
        fname = blob[fh + 7: blob.find("*/", fh)] if fh >= 0 else "?"
        line = blob.count("\n", blob.find("*/", fh) + 2 if fh >= 0 else 0, pos) + 1
        return f"{fname}:{line}"

    checks: List[dict] = []

    def add(check_id: str, status: str, detail: str, ev: Optional[str] = None) -> None:
        checks.append({"id": check_id, "status": status, "detail": detail,
                       "evidence": ev})

    # 1. tokenized :focus-visible ring
    fv = re.search(r":focus-visible[^{}]*\{([^{}]*)\}", css_all)
    if not fv:
        add("focus-visible-ring", "FAIL", "no :focus-visible rule — keyboard focus falls to the browser default (design.md v2 focus contract)")
    elif "var(--" in fv.group(1) and "outline" in fv.group(1):
        add("focus-visible-ring", "PASS", "tokenized :focus-visible outline present",
            evidence(css_all, r":focus-visible[^{}]*\{"))
    else:
        add("focus-visible-ring", "WARN", "a :focus-visible rule exists but its outline is not tokenized",
            evidence(css_all, r":focus-visible[^{}]*\{"))

    # 2. prefers-reduced-motion
    if re.search(r"@media[^{]*prefers-reduced-motion", css_all):
        add("reduced-motion", "PASS", "prefers-reduced-motion block present",
            evidence(css_all, r"@media[^{]*prefers-reduced-motion"))
    else:
        add("reduced-motion", "FAIL", "no prefers-reduced-motion handling (design.md v2 Motion section)")

    # 3. desktop measure (centered 772px column)
    if re.search(r"max-width:\s*772px", css_all):
        add("desktop-measure", "PASS", "content measure capped at the fleet 772px",
            evidence(css_all, r"max-width:\s*772px"))
    else:
        near = re.search(r"max-width:\s*(6\d\d|7\d\d|8\d\d)px", css_all)
        if near:
            add("desktop-measure", "WARN",
                f"content capped at {near.group(0).split(':')[1].strip()} — spec is 772px",
                evidence(css_all, r"max-width:\s*(6\d\d|7\d\d|8\d\d)px"))
        else:
            add("desktop-measure", "FAIL", "no desktop content cap found — spec: centered max-width 772px")

    # 4. switch on-track color (THE green decision)
    sw = re.search(r"\.toggle\.on[^{}]*\{([^{}]*)\}", css_all)
    if not sw:
        add("switch-on-green", "NA", "no .toggle.on rule found (app may not ship a switch)")
    else:
        body = sw.group(1)
        ev = evidence(css_all, r"\.toggle\.on[^{}]*\{")
        if re.search(r"var\(--(on|success)\b", body):
            add("switch-on-green", "PASS", "switch on-track uses the success token", ev)
        elif "var(--accent" in body:
            add("switch-on-green", "FAIL", "switch on-track is the accent — design.md v2 says success (green)", ev)
        else:
            add("switch-on-green", "WARN", f"switch on-track is not tokenized: {body.strip()[:60]}", ev)

    # 5. native checkboxes (the one-boolean-control rule)
    cb = re.search(r"type=[\"']checkbox[\"']", markup_all)
    if cb:
        n = len(re.findall(r"type=[\"']checkbox[\"']", markup_all))
        add("no-native-checkbox", "FAIL",
            f"{n} native checkbox(es) — the fleet boolean control is the switch",
            evidence(markup_all, r"type=[\"']checkbox[\"']"))
    else:
        add("no-native-checkbox", "PASS", "no native checkboxes")

    # 6. disclosure closed-box contract (only if the app has disclosures)
    if re.search(r"<details|\.collapse-summary|details\[open\]", css_all + markup_all):
        h = re.search(r"height:\s*52px", css_all)
        p = re.search(r"padding:\s*0 14px", css_all)
        d = re.search(r"\[open\][^{}]*summary[^{}]*\{[^{}]*border-bottom", css_all)
        missing = [name for name, hit in
                   (("closedHeight 52px", h), ("summaryPadding 0 14px", p),
                    ("open divider", d)) if not hit]
        if not missing:
            add("disclosure-box", "PASS", "52px closed box + 0 14px padding + open divider all present",
                evidence(css_all, r"height:\s*52px"))
        else:
            add("disclosure-box", "FAIL", "disclosure contract incomplete — missing: " + ", ".join(missing))
    else:
        add("disclosure-box", "NA", "no details/summary disclosures found")

    # 7. native <dialog> vs hand-rolled overlay
    if re.search(r"<dialog|\.showModal\(", markup_all):
        add("native-dialog", "PASS", "native <dialog> / showModal() in use",
            evidence(markup_all, r"<dialog|\.showModal\("))
    elif re.search(r"class=[\"'][^\"']*(modal|overlay)", markup_all):
        add("native-dialog", "WARN", "modal/overlay classes without native <dialog> — hand-rolled dialog suspected",
            evidence(markup_all, r"class=[\"'][^\"']*(modal|overlay)"))
    else:
        add("native-dialog", "NA", "no dialogs found")

    # 8. nav contract signals — architecture decides, not provenance. A vendored
    #    copy still needs the app-side standalone shell (#303), and a hand-carried
    #    nav with the full architecture passes on merit (home-automation is the
    #    reference and is hand-carried). Provenance is the `vendored` lens's job.
    signals = {
        "hide-under-dialog (body:has(dialog[open]))": r"body:has\(dialog\[open\]\)",
        "viewport anchor (100dvh)": r"100dvh",
        "safe-area inset": r"safe-area-inset-bottom",
    }
    missing = [name for name, pat in signals.items()
               if not re.search(pat, css_all)]
    shell_ok = standalone_shell_present(css_all)
    n_signals = len(signals) + 1  # + the standalone shell
    vendored_root = find_vendored_root(root)
    vendored_nav = vendored_root is not None and (vendored_root / "nav" / "nav-tabs.css").exists()
    provenance = "vendored" if vendored_nav else "hand-carried"
    # nav-nesting: nav.tabs must be a <body> sibling of main.app, never a
    # descendant (_vendored/nav/README.md; app-launcher#369) — a structural
    # violation the CSS signals above can't see, so it FAILs on its own
    # regardless of everything else passing.
    index_files_nc = [p for p in html_files if p.name == "index.html"]
    if nav_nested_in_app(index_files_nc):
        add("nav-contract", "FAIL",
            "nav-nesting: nested-inside-app — <nav class=\"tabs\"> must be a "
            "direct <body> sibling of <main class=\"app\">, never nested "
            "inside it (_vendored/nav/README.md; app-launcher#369)")
    elif not missing and shell_ok:
        add("nav-contract", "PASS",
            f"nav-nesting: sibling (PASS); nav contract signals + standalone "
            f"fixed-inset shell all present ({provenance})")
    elif not missing and not shell_ok:
        add("nav-contract", "WARN",
            "app shell lacks the standalone fixed-inset scroller (design.md nav "
            "contract, home-automation#303) — the scroll bug persists; adopt "
            "_vendored/nav/ plus the fixed-inset .app shell")
    elif len(missing) + (0 if shell_ok else 1) < n_signals:
        if not shell_ok:
            missing.append("standalone fixed-inset .app scroller (#303)")
        add("nav-contract", "WARN", "nav contract partially present — missing: " + ", ".join(missing))
    else:
        add("nav-contract", "FAIL", "no nav-contract signals — adopt the vendored nav from project-scaffolding")

    # 9. icon-size strays vs the spec's icons.size steps
    allowed = set()
    for key, val in spec_light.items():
        if key.startswith("icons.size.") and val.endswith("px"):
            allowed.add(val)
    if allowed:
        strays: Dict[str, int] = {}
        for bm in _BLOCK_RE.finditer(css_all):
            selector = bm.group(1)
            if "icon" not in selector:
                continue
            # `.header-icon-btn { width: 40px }` is a BUTTON box, not a glyph —
            # icon-named button/control selectors are not icon-size findings.
            if re.search(r"btn|button", selector):
                continue
            for dm in re.finditer(r"\b(?:width|height):\s*(\d+px)", bm.group(2)):
                if dm.group(1) not in allowed:
                    strays[dm.group(1)] = strays.get(dm.group(1), 0) + 1
        if strays:
            top = ", ".join(f"{k}x{v}" for k, v in sorted(strays.items(), key=lambda kv: -kv[1])[:8])
            add("icon-sizes", "WARN",
                f"icon px sizes outside the icons.size steps ({', '.join(sorted(allowed))}): {top}")
        else:
            add("icon-sizes", "PASS", "all fixed icon sizes on the icons.size steps")
    else:
        add("icon-sizes", "NA", "spec defines no icons.size steps")

    # 10. viewport zoom lock — installable PWAs pin the scale (design.md Layout;
    #     fleet-config#296): user-scalable=no + maximum-scale=1, plus
    #     viewport-fit=cover for the safe-area insets the nav contract needs.
    index_files = [p for p in html_files if p.name == "index.html"]
    if not index_files:
        add("viewport-lock", "NA", "no index.html found")
    else:
        broken: List[str] = []
        uncovered: List[str] = []
        for p in index_files:
            text = strip_comments(read_text(p), "html")
            m = re.search(r"<meta[^>]+name=[\"']viewport[\"'][^>]*>", text, re.I)
            tag = m.group(0) if m else ""
            if not m or "user-scalable=no" not in tag or "maximum-scale=1" not in tag:
                broken.append(rel(root, p))
            elif "viewport-fit=cover" not in tag:
                uncovered.append(rel(root, p))
        if broken:
            add("viewport-lock", "FAIL",
                "viewport meta lacks the zoom lock (user-scalable=no + "
                "maximum-scale=1 — design.md Layout, fleet-config#296): "
                + ", ".join(broken))
        elif uncovered:
            add("viewport-lock", "WARN",
                "zoom lock present but viewport-fit=cover missing (safe-area "
                "insets need it): " + ", ".join(uncovered))
        else:
            add("viewport-lock", "PASS",
                f"zoom lock (user-scalable=no, maximum-scale=1, viewport-fit=cover) on {len(index_files)} index.html")

    # 11. button tiers — the fleet button vocabulary (design.md Components;
    #     fleet-config#296). Hardcoded fills and a filled "ghost" are FAILs;
    #     a solid accent outside the primary and a tint without accent text
    #     are WARNs for /design-sync's judgment layer to arbitrate.
    hardcoded: List[str] = []
    ghost_inverted: List[str] = []
    solid_strays: List[str] = []
    tint_off: List[str] = []
    n_btn = 0
    for bm in _BLOCK_RE.finditer(css_all):
        sel = bm.group(1).strip().splitlines()[-1].strip()
        if not re.search(r"btn|button", sel, re.I):
            continue
        n_btn += 1
        decls = {p.lower(): v.strip()
                 for p, v in _ANY_DECL_RE.findall(bm.group(2) + ";")}
        for prop in ("background", "background-color", "color", "border",
                     "border-color", "border-top", "border-bottom"):
            v = decls.get(prop)
            if v and "var(" not in v and _COLOR_LITERAL_RE.search(v):
                hardcoded.append(f"{sel} {{ {prop}: {v[:40]} }}")
        bg = decls.get("background", decls.get("background-color", ""))
        # base ghost rule only — a trailing state class (.copied, .danger-flash)
        # or pseudo is a legitimate state flash, not the resting recipe
        if (re.search(r"\.[A-Za-z0-9_-]*ghost[A-Za-z0-9_-]*$", sel) and bg
                and bg.split()[0] not in ("transparent", "none")):
            ghost_inverted.append(f"{sel} {{ background: {bg[:40]} }}")
        if bg == "var(--accent)" and not re.search(r"detail-save|primary", sel):
            solid_strays.append(sel)
        if "var(--accent-soft)" in bg:
            col = decls.get("color")
            if col and col != "var(--accent)":
                tint_off.append(f"{sel} {{ color: {col[:30]} }}")
    bits = []
    if hardcoded:
        bits.append(f"{len(hardcoded)} hardcoded button color(s): "
                    + "; ".join(hardcoded[:4]))
    if ghost_inverted:
        bits.append(f"{len(ghost_inverted)} ghost class(es) with a fill "
                    "(ghost = transparent; a tinted fill is the tint tier): "
                    + "; ".join(ghost_inverted[:4]))
    if solid_strays:
        bits.append("solid accent fill outside the primary: "
                    + ", ".join(solid_strays[:6]))
    if tint_off:
        bits.append("tint fill without accent text: "
                    + "; ".join(tint_off[:4]))
    if n_btn == 0:
        add("button-tiers", "NA", "no button rules found")
    elif hardcoded or ghost_inverted:
        add("button-tiers", "FAIL", " | ".join(bits))
    elif bits:
        add("button-tiers", "WARN", " | ".join(bits))
    else:
        add("button-tiers", "PASS",
            f"{n_btn} button rule(s) conform to the tier vocabulary")

    # 12. user-selectable theme — pre-paint data-theme boot + persisted .theme
    #     toggle + dual scheme-gated theme-color metas (design.md Colors "Theme
    #     switching"; fleet-config#290). Grep-level only — whether the glyph
    #     shows the action stays LLM judgment in /design-sync step 4. Both
    #     stamp idioms (dataset.theme / setAttribute) and both key shapes
    #     (`.theme` literal / a theme-named constant) are canonical.
    if not index_files:
        add("theme-toggle", "NA", "no index.html found")
    else:
        stamp_re = r"dataset\.theme|setAttribute\(\s*['\"]data-theme['\"]"
        toggle_re = (r"localStorage\.setItem\(\s*"
                     r"(?:['\"][^'\"]*\.theme['\"]|\w*theme\w*\s*,)")
        missing_boot: List[str] = []
        meta_gaps: List[str] = []
        for p in index_files:
            text = strip_comments(read_text(p), "html")
            body_at = text.lower().find("<body")
            head = text[:body_at] if body_at >= 0 else text
            boot = (re.search(stamp_re, head)
                    and re.search(r"localStorage\.getItem\(\s*['\"][^'\"]*\.theme['\"]", head)
                    and "prefers-color-scheme" in head)
            if not boot:
                missing_boot.append(rel(root, p))
                continue
            light_meta = dark_meta = None
            for mm in re.finditer(r"<meta\b[^>]*name=[\"']theme-color[\"'][^>]*>",
                                  text, re.I):
                tag = mm.group(0)
                media = re.search(r"media=[\"']([^\"']*)[\"']", tag)
                content = re.search(r"content=[\"']([^\"']*)[\"']", tag)
                if not media or "prefers-color-scheme" not in media.group(1):
                    continue
                if "light" in media.group(1):
                    light_meta = content.group(1) if content else ""
                elif "dark" in media.group(1):
                    dark_meta = content.group(1) if content else ""
            if light_meta is None or dark_meta is None:
                meta_gaps.append(f"{rel(root, p)} (missing the scheme-gated "
                                 "theme-color meta pair)")
                continue
            want_light = normalize_value(spec_light.get("colors.canvas", ""))
            want_dark = normalize_value((spec_dark or {}).get("colors.canvas", ""))
            if want_light and normalize_value(light_meta) != want_light:
                meta_gaps.append(f"{rel(root, p)} (light theme-color "
                                 f"{light_meta} != spec canvas {want_light})")
            if want_dark and normalize_value(dark_meta) != want_dark:
                meta_gaps.append(f"{rel(root, p)} (dark theme-color "
                                 f"{dark_meta} != spec dark canvas {want_dark})")
        if missing_boot:
            add("theme-toggle", "FAIL",
                "no pre-paint data-theme boot script in <head> (localStorage "
                ".theme key + prefers-color-scheme fallback — design.md Colors "
                "theme switching, fleet-config#290): " + ", ".join(missing_boot))
        elif not re.search(toggle_re, markup_all, re.I):
            add("theme-toggle", "FAIL",
                "boot script present but no persisted theme toggle — no "
                "localStorage setItem on a .theme key (or theme-named constant) "
                "anywhere (design.md Colors theme switching, fleet-config#290)")
        elif meta_gaps:
            add("theme-toggle", "WARN",
                "theme mechanism present but the theme-color metas drift: "
                + "; ".join(meta_gaps))
        else:
            add("theme-toggle", "PASS",
                "pre-paint boot + persisted .theme toggle + dual theme-color "
                f"metas on {len(index_files)} index.html",
                evidence(markup_all, toggle_re, re.I))

    # 13. icon-set — one set, vendored Lucide sprite, never hand-drawn/mixed
    #     (design.md Icons). Emoji glyphs anywhere in rendered UI-chrome text
    #     (markup text nodes or JS string literals) are the anti-pattern; the
    #     lucide-sprite adoption signal is the vendored `icons/` component
    #     (fleet-config#284, app-launcher#355/#368).
    lucide_adopted = (vendored_root is not None
                       and (vendored_root / "icons").is_dir())
    emoji_sites = find_emoji_sites(root, html_files, js_files)
    if emoji_sites and not lucide_adopted:
        add("icon-set", "FAIL",
            f"icon-set: emoji-glyphs ({len(emoji_sites)} site(s)) / "
            "lucide-sprite: NOT_ADOPTED (design.md Icons — one set, never "
            "hand-drawn/mixed): " + ", ".join(emoji_sites[:8]))
    elif emoji_sites and lucide_adopted:
        add("icon-set", "WARN",
            f"icon-set: emoji-glyphs ({len(emoji_sites)} site(s)) alongside "
            "an adopted lucide-sprite — mixed icon set: " + ", ".join(emoji_sites[:8]))
    elif lucide_adopted:
        add("icon-set", "PASS",
            "icon-set: lucide-sprite adopted, no emoji glyphs in rendered text")
    else:
        add("icon-set", "NA", "no emoji glyphs and no vendored icons/ component found")

    # 14. chevron placement — disclosure summaries pin the chevron right,
    #     never a leading arrow (design.md disclosure.chevron: right;
    #     app-launcher#362 shipped a leading `›` the lint never caught).
    leading_chevrons: List[str] = []
    trailing_ok = 0
    for sm in re.finditer(r"<summary\b[^>]*>(.*?)</summary>", markup_all, re.S | re.I):
        content = sm.group(1)
        chevron_m = (re.search(r'class=["\'][^"\']*chevron[^"\']*["\']', content, re.I)
                     or re.search(r"[›⌄▾▸❱➤]", content))
        if not chevron_m:
            continue
        title_m = None
        for tm in re.finditer(r">([^<>]*[A-Za-z0-9][^<>]*)<", content):
            text_run = tm.group(1)
            if (re.fullmatch(r"[\s›⌄▾▸❱➤]*", text_run)):
                continue  # the chevron glyph's own text run
            title_m = tm
            break
        if title_m is None:
            continue
        if chevron_m.start() < title_m.start():
            leading_chevrons.append(loc_at(markup_all, sm.start(1) + chevron_m.start()))
        else:
            trailing_ok += 1
    if leading_chevrons:
        add("chevron-placement", "FAIL",
            f"{len(leading_chevrons)} disclosure(s) with a leading chevron "
            "(design.md disclosure.chevron: right — never a leading arrow): "
            + ", ".join(leading_chevrons[:6]))
    elif trailing_ok:
        add("chevron-placement", "PASS",
            f"chevron right-pinned on {trailing_ok} disclosure(s)")
    else:
        add("chevron-placement", "NA", "no chevron-bearing disclosures found")

    # 15. row-height scale — repeating list/action-rail rows draw their
    #     height from the 3-step scale (rows.sm 44px / rows.md 52px /
    #     rows.lg 60px), not an ad hoc literal (design.md Components
    #     list-row; app-launcher#365/PR#380).
    allowed_row_px = set()
    for key, val in spec_light.items():
        if key.startswith("rows.") and val.endswith("px"):
            allowed_row_px.add(val)
    if not allowed_row_px:
        allowed_row_px = {"44px", "52px", "60px"}
    row_strays: Dict[str, int] = {}
    n_row_rules = 0
    for bm in _BLOCK_RE.finditer(css_all):
        selector = bm.group(1)
        sel_low = selector.lower()
        if "row" not in sel_low and "action-rail" not in sel_low:
            continue
        n_row_rules += 1
        for dm in re.finditer(r"\b(?:min-height|height):\s*([^;]+);", bm.group(2)):
            val = dm.group(1).strip()
            if val.startswith("var(") or "calc(" in val:
                continue
            if not re.fullmatch(r"\d+px", val):
                continue
            if val not in allowed_row_px:
                row_strays[val] = row_strays.get(val, 0) + 1
    if row_strays:
        top = ", ".join(f"{k}x{v}" for k, v in sorted(row_strays.items(), key=lambda kv: -kv[1])[:8])
        add("row-height-scale", "WARN",
            f"row/action-rail heights outside the {', '.join(sorted(allowed_row_px))} "
            f"scale: {top}")
    elif n_row_rules:
        add("row-height-scale", "PASS",
            f"all fixed row/action-rail heights on the {', '.join(sorted(allowed_row_px))} scale")
    else:
        add("row-height-scale", "NA", "no row/action-rail height rules found")

    # 16-20. Editor-modal contract (design.md `modal` component;
    #     fleet-config#307) — a <dialog> containing a real <form> is held to
    #     the header/rows/fieldset/footer/top-anchor contract.
    modals = _editor_modals(root, html_files)
    if not modals:
        for cid in ("modal-unstyled-rows", "modal-raw-fieldset", "modal-header",
                    "modal-footer", "modal-top-anchor"):
            add(cid, "NA", "no editor-modal <dialog> found")
    else:
        # 16. unstyled dialog rows — a row class used inside a dialog that is
        #     only ever styled under some other, unrelated scope.
        unstyled: List[str] = []
        for modal in modals:
            for lm in _LABEL_CLASS_RE.finditer(modal["inner"]):
                for cls in lm.group(1).split():
                    scopes = _class_scope_status(css_all, cls)
                    if "global" in scopes or "dialog" in scopes:
                        continue
                    loc = _loc_in_modal(modal, lm.start())
                    why = "styled only outside dialogs" if scopes else "never styled"
                    unstyled.append(f"{loc} label.{cls} ({why})")
        if unstyled:
            add("modal-unstyled-rows", "FAIL",
                f"{len(unstyled)} dialog row class(es) with no dialog-scoped "
                "styling (design.md modal contract): " + "; ".join(unstyled[:6]))
        else:
            add("modal-unstyled-rows", "PASS",
                "every dialog row class is styled globally or in a dialog-scoped rule")

        # 17. raw <fieldset> — a fieldset/legend with no authored CSS at all,
        #     rendering as a raw browser legend box.
        raw_fieldsets: List[str] = []
        for modal in modals:
            for fm in _FIELDSET_OPEN_RE.finditer(modal["inner"]):
                cls_m = _TAG_CLASS_RE.search(fm.group(1))
                classes = cls_m.group(1).split() if cls_m else []
                if classes:
                    authored = any(_class_scope_status(css_all, c) for c in classes)
                else:
                    authored = bool(_selector_hits(css_all, _FIELDSET_TAG_PAT))
                if not authored:
                    raw_fieldsets.append(_loc_in_modal(modal, fm.start()))
        if raw_fieldsets:
            add("modal-raw-fieldset", "FAIL",
                f"{len(raw_fieldsets)} <fieldset> with no authored CSS — raw "
                "browser legend box (design.md modal wants titled plain "
                "sections, never a fieldset): " + ", ".join(raw_fieldsets[:6]))
        else:
            add("modal-raw-fieldset", "PASS", "no unstyled <fieldset> found in editor modals")

        # 18. header contract — a title needs a square × close button; a
        #     footer "Cancel" button in its place is the anti-pattern.
        header_bad: List[str] = []
        header_checked = 0
        for modal in modals:
            if not re.search(r"<h[1-6]\b", modal["inner"], re.I):
                continue
            header_checked += 1
            has_close = False
            has_cancel = False
            for bm2 in _BUTTON_RE.finditer(modal["inner"]):
                battrs, btext = bm2.group(1), bm2.group(2)
                if (re.search(r'aria-label=["\']close["\']', battrs, re.I)
                        or re.search(r'class=["\'][^"\']*\bclose\b[^"\']*["\']', battrs, re.I)):
                    has_close = True
                text_clean = re.sub(r"<[^>]+>", "", btext).strip()
                if re.fullmatch(r"cancel", text_clean, re.I):
                    has_cancel = True
            if not has_close or has_cancel:
                reasons = []
                if not has_close:
                    reasons.append("no square x close button")
                if has_cancel:
                    reasons.append("footer Cancel button in its place")
                header_bad.append(f"{modal['file']}:{modal['line']} " + " + ".join(reasons))
        if header_checked == 0:
            add("modal-header", "NA", "no titled editor-modal <dialog> found")
        elif header_bad:
            add("modal-header", "FAIL",
                "editor-modal header contract violated (design.md modal wants "
                "a heading-lg title + square x close, never a footer Cancel): "
                + "; ".join(header_bad[:6]))
        else:
            add("modal-header", "PASS", "editor-modal header(s) carry a square x close, no footer Cancel")

        # 19. footer contract — exactly one always-visible action, styled as
        #     the full-width solid-accent primary.
        footer_bad: List[str] = []
        footer_checked = 0
        for modal in modals:
            footer = None
            for fm2 in _FOOTER_CONTAINER_RE.finditer(modal["inner"]):
                fattrs, fbody = fm2.group(2), fm2.group(3)
                fcls_m = _TAG_CLASS_RE.search(fattrs)
                fclasses = fcls_m.group(1).split() if fcls_m else []
                if any(re.search(r"actions|footer", c, re.I) for c in fclasses):
                    footer = (fclasses, fbody)
                    break
            if footer is None:
                continue
            footer_checked += 1
            fclasses, fbody = footer
            visible = [bm2 for bm2 in _BUTTON_RE.finditer(fbody)
                       if not re.search(r"(^|\s)hidden(\s|=|$)", bm2.group(1), re.I)]
            if len(visible) > 1:
                footer_bad.append(f"{modal['file']}:{modal['line']} "
                                   f"{len(visible)} always-visible footer actions")
                continue
            if len(visible) == 0:
                continue
            btn_cls_m = _TAG_CLASS_RE.search(visible[0].group(1))
            btn_classes = btn_cls_m.group(1).split() if btn_cls_m else []
            is_accent = False
            is_full_width = False
            for c in btn_classes:
                for _, _anc, body in _selector_hits(css_all, re.compile(r"\." + re.escape(c) + r"\b")):
                    if re.search(r"background(-color)?:\s*var\(--accent\)", body):
                        is_accent = True
                    if re.search(r"width:\s*100%|flex:\s*1\b|align-self:\s*stretch", body):
                        is_full_width = True
            for c in fclasses:
                for _, _anc, body in _selector_hits(css_all, re.compile(r"\." + re.escape(c) + r"\b")):
                    if (re.search(r"flex-direction:\s*column", body)
                            and re.search(r"align-items:\s*stretch", body)):
                        is_full_width = True
            if not (is_accent and is_full_width):
                missing = []
                if not is_accent:
                    missing.append("not the solid-accent primary recipe")
                if not is_full_width:
                    missing.append("not full-width")
                footer_bad.append(f"{modal['file']}:{modal['line']} primary " + " + ".join(missing))
        if footer_checked == 0:
            add("modal-footer", "NA", "no locatable footer/actions container in any editor modal")
        elif footer_bad:
            add("modal-footer", "FAIL",
                "editor-modal footer contract violated (design.md modal wants "
                "exactly one full-width solid-accent primary): "
                + "; ".join(footer_bad[:6]))
        else:
            add("modal-footer", "PASS",
                "editor-modal footer(s) carry exactly one full-width solid-accent primary")

        # 20. top-anchoring — a tall form must not jump vertically as
        #     conditional rows toggle; the dialog itself scrolls internally.
        top_anchor_bad: List[str] = []
        for modal in modals:
            ok = False
            for c in modal["classes"]:
                for _, _anc, body in _selector_hits(css_all, re.compile(r"\." + re.escape(c) + r"\b")):
                    if re.search(r"max-height", body) and re.search(r"overflow(-y)?:\s*auto", body):
                        ok = True
            if not ok:
                for _, _anc, body in _selector_hits(css_all, re.compile(r"^dialog(?:[.:#\[]|$)", re.I)):
                    if re.search(r"max-height", body) and re.search(r"overflow(-y)?:\s*auto", body):
                        ok = True
            if not ok:
                top_anchor_bad.append(f"{modal['file']}:{modal['line']}")
        if top_anchor_bad:
            add("modal-top-anchor", "FAIL",
                "editor-modal(s) with no max-height + internal scroll — a tall "
                "form jumps as conditional rows toggle (design.md modal: "
                "top-anchored on mobile): " + ", ".join(top_anchor_bad[:6]))
        else:
            add("modal-top-anchor", "PASS",
                "editor-modal(s) are top-anchored with internal scroll")

    return checks


# ---------------------------------------------------------------- vendored

_SPRITE_SYMBOL_RE = re.compile(
    r'<symbol\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>.*?</symbol>', re.S)


def _sprite_symbols(text: str) -> Dict[str, str]:
    return {m.group(1): m.group(0) for m in _SPRITE_SYMBOL_RE.finditer(text)}


def compare_icon_sprite(app_text: str, ref_text: str) -> Optional[str]:
    """Per-symbol compare for `icons-sprite.html`, not whole-file digest.

    The icons component's own README sanctions per-app symbol-set trimming
    (an app only inlines the Lucide glyphs it actually uses) — a whole-file
    byte-compare flags any trimmed sprite as FORKED even when every symbol it
    kept is byte-identical to the scaffold's (fleet-config#284 finding 4).
    Returns None (caller falls back to a whole-file digest) when either side
    has no `<symbol id="...">` elements to compare.
    """
    app_syms = _sprite_symbols(app_text)
    ref_syms = _sprite_symbols(ref_text)
    if not app_syms or not ref_syms:
        return None
    mismatched = [sid for sid, body in app_syms.items()
                  if ref_syms.get(sid) != body]
    if mismatched:
        return "FORKED"
    if len(app_syms) < len(ref_syms):
        return "IDENTICAL (trimmed)"
    return "IDENTICAL"


def vendored(root: Path, scaffold: Path) -> Dict[str, object]:
    """Byte-compare the app's _vendored component copies against the scaffold."""
    app_dir = find_vendored_root(root)
    ref_dir = scaffold / "app/webapp/static/_vendored"
    if not ref_dir.is_dir():
        return {"error": f"scaffold _vendored not found at {ref_dir}"}
    result: Dict[str, object] = {"components": {}, "app_has_vendored_dir": app_dir is not None}
    comps: Dict[str, object] = result["components"]  # type: ignore[assignment]

    def digest(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    for comp in sorted(d for d in ref_dir.iterdir() if d.is_dir()):
        app_comp = (app_dir / comp.name) if app_dir else None
        if not app_comp or not app_comp.is_dir():
            comps[comp.name] = {"status": "NOT_ADOPTED", "files": {}}
            continue
        files: Dict[str, str] = {}
        forked = False
        for ref_file in sorted(comp.rglob("*")):
            if not ref_file.is_file():
                continue
            relf = ref_file.relative_to(comp).as_posix()
            app_file = app_comp / relf
            if not app_file.is_file():
                files[relf] = "MISSING"
                forked = True
                continue
            sprite_status = (compare_icon_sprite(read_text(app_file), read_text(ref_file))
                              if relf == "icons-sprite.html" else None)
            if sprite_status is not None:
                files[relf] = sprite_status
                forked = forked or sprite_status == "FORKED"
            elif digest(app_file) == digest(ref_file):
                files[relf] = "IDENTICAL"
            else:
                files[relf] = "FORKED"
                forked = True
        # app-side extras are allowed (e.g. an app-specific sprite) — note them
        for app_file in sorted(app_comp.rglob("*")):
            if app_file.is_file():
                relf = app_file.relative_to(app_comp).as_posix()
                files.setdefault(relf, "APP_ONLY")
        comps[comp.name] = {"status": "FORKED" if forked else "IDENTICAL", "files": files}
    return result


# ---------------------------------------------------------------- siblings

_DEF_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|"
    r"^(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\()",
    re.M,
)
_SIBLING_STOPLIST = {"init", "main", "render", "update"}


def siblings(root: Path, js_files: List[Path]) -> List[dict]:
    """Top-level definitions with the same name in >=2 JS files."""
    defs: Dict[str, List[str]] = {}
    for path in js_files:
        text = strip_comments(read_text(path), "js")
        for m in _DEF_RE.finditer(text):
            name = m.group(1) or m.group(2)
            if not name or name in _SIBLING_STOPLIST or len(name) < 3:
                continue
            line = text[: m.start()].count("\n") + 1
            defs.setdefault(name, []).append(f"{rel(root, path)}:{line}")
    dupes = [{"name": name, "sites": sites}
             for name, sites in sorted(defs.items())
             if len({s.split(":")[0] for s in sites}) >= 2]
    return dupes


# --------------------------------------------------------------------- CLI

def _load_specs(args: argparse.Namespace) -> Tuple[Dict[str, str], Dict[str, str]]:
    home = Path.home() / ".claude"
    spec_light = Path(args.spec) if args.spec else home / "design.md"
    spec_dark = Path(args.spec_dark) if args.spec_dark else home / "design.dark.md"
    return parse_spec(read_text(spec_light)), parse_spec(read_text(spec_dark))


def _app_props(root: Path, css_files: List[Path]) -> Tuple[Dict[str, Dict[str, Tuple[str, int]]], str]:
    """Merge custom props across stylesheets; the file with the most wins naming."""
    merged: Dict[str, Dict[str, Tuple[str, int]]] = {"light": {}, "dark": {}}
    main_file, main_count = "", -1
    for path in css_files:
        themes = parse_custom_props(read_text(path), path.name)
        count = len(themes["light"]) + len(themes["dark"])
        if count > main_count:
            main_file, main_count = rel(root, path), count
        for theme in ("light", "dark"):
            for k, v in themes[theme].items():
                merged[theme].setdefault(k, v)
    return merged, main_file


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic design-system lint for /design-sync v2.")
    ap.add_argument("command", choices=["tokens", "adoption", "contracts", "vendored", "siblings", "all"])
    ap.add_argument("root", help="target repo root")
    ap.add_argument("--spec", help="override light spec path (tests)")
    ap.add_argument("--spec-dark", help="override dark spec path (tests)")
    ap.add_argument("--scaffold", default="E:/automation/project-scaffolding",
                    help="project-scaffolding root for the vendored byte-compare")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"not a directory: {root}"}))
        return 2

    css_files = repo_files(root, (".css",))
    html_files = repo_files(root, (".html",))
    js_files = repo_files(root, (".js",))

    out: Dict[str, object] = {}
    if args.command in ("tokens", "contracts", "all"):
        spec_light, spec_dark = _load_specs(args)
    if args.command in ("tokens", "all"):
        app, main_file = _app_props(root, css_files)
        out["tokens"] = map_tokens(spec_light, spec_dark, app, main_file)
    if args.command in ("adoption", "all"):
        out["adoption"] = adoption(root, css_files)
    if args.command in ("contracts", "all"):
        out["contracts"] = contracts(root, css_files, html_files, js_files,
                                     spec_light, spec_dark)
    if args.command in ("vendored", "all"):
        out["vendored"] = vendored(root, Path(args.scaffold))
    if args.command in ("siblings", "all"):
        out["siblings"] = siblings(root, js_files)

    if args.command != "all" and len(out) == 1:
        out = next(iter(out.values()))  # type: ignore[assignment]
    print(json.dumps(out, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
