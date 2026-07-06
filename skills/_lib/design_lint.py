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


def contracts(
    root: Path,
    css_files: List[Path],
    html_files: List[Path],
    js_files: List[Path],
    spec_light: Dict[str, str],
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

    # 8. nav contract signals (vendored copy, or the load-bearing rules)
    if (root / "app/webapp/static/_vendored/nav/nav-tabs.css").exists():
        add("nav-contract", "PASS", "vendored nav present (_vendored/nav/)")
    else:
        signals = {
            "hide-under-dialog (body:has(dialog[open]))": r"body:has\(dialog\[open\]\)",
            "viewport anchor (100dvh)": r"100dvh",
            "safe-area inset": r"safe-area-inset-bottom",
        }
        missing = [name for name, pat in signals.items()
                   if not re.search(pat, css_all)]
        if not missing:
            add("nav-contract", "PASS", "nav contract signals all present (hand-carried)")
        elif len(missing) < len(signals):
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

    return checks


# ---------------------------------------------------------------- vendored

def vendored(root: Path, scaffold: Path) -> Dict[str, object]:
    """Byte-compare the app's _vendored component copies against the scaffold."""
    app_dir = root / "app/webapp/static/_vendored"
    ref_dir = scaffold / "app/webapp/static/_vendored"
    if not ref_dir.is_dir():
        return {"error": f"scaffold _vendored not found at {ref_dir}"}
    result: Dict[str, object] = {"components": {}, "app_has_vendored_dir": app_dir.is_dir()}
    comps: Dict[str, object] = result["components"]  # type: ignore[assignment]

    def digest(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    for comp in sorted(d for d in ref_dir.iterdir() if d.is_dir()):
        app_comp = app_dir / comp.name
        if not app_comp.is_dir():
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
        out["contracts"] = contracts(root, css_files, html_files, js_files, spec_light)
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
