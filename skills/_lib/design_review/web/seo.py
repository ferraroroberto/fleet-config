"""Search facts of a web page, read over HTTP from the static preview (design.web.md section 10).

Per URL: title and meta description (length, unique across the sitemap),
canonical, Open Graph and Twitter card (with the social image's real size),
structured data, noindex and broken internal links. Per site: `sitemap.xml`
against the site's indexable pages, `robots.txt`, one-hop 301s for every
`_redirects` rule, and a 404 status for a missing URL.

Everything goes through the preview the review started, on loopback, so the
URL rules are the host's (pretty URLs, `_redirects`, the 404 page). Stdlib only.
Each metric is a list of findings; an empty list passes.
"""
from __future__ import annotations

import http.client
import json
import re
import struct
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatch
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

TIMEOUT_S = 15.0
MAX_SITEMAP_PAGES = 300
MAX_LINKS = 400
SOCIAL_CARD = (1200, 630)
# The JSON-LD each page type needs, and the properties each type needs to be valid (Google's required set).
REQUIRED_TYPES = {"home": ({"Person", "Organization"}, "Person or Organization"),
                  "product": ({"Product"}, "Product"),
                  "article": ({"Article", "BlogPosting", "NewsArticle", "VideoObject"}, "Article or VideoObject")}
REQUIRED_PROPS = {"Person": ("name",), "Organization": ("name",), "Product": ("name", "offers"),
                  "Article": ("headline",), "BlogPosting": ("headline",), "NewsArticle": ("headline",),
                  "VideoObject": ("name", "thumbnailUrl", "uploadDate")}


@dataclass
class Response:
    status: int
    headers: Dict[str, str]
    body: bytes


Fetch = Callable[[str], Response]


def http_fetch(base_url: str) -> Fetch:
    """A GET against the preview that never follows redirects (a hop is a fact to check, not to hide)."""
    parts = urlsplit(base_url)

    def fetch(path: str) -> Response:
        conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=TIMEOUT_S)
        try:
            conn.request("GET", path or "/")
            r = conn.getresponse()
            return Response(r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        except (OSError, http.client.HTTPException) as exc:
            return Response(0, {"x-error": str(exc)}, b"")
        finally:
            conn.close()
    return fetch


@dataclass
class PageFacts:
    lang: str = ""
    title: Optional[str] = None
    description: Optional[str] = None
    robots: str = ""
    canonical: Optional[str] = None
    meta: Dict[str, str] = field(default_factory=dict)  # og:* and twitter:* by property/name
    ld_json: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.facts = PageFacts()
        self._in_title = False
        self._ld: Optional[List[str]] = None
        self._title: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        f = self.facts
        if tag == "html":
            f.lang = a.get("lang", "")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key == "description":
                f.description = a.get("content", "")
            elif key == "robots":
                f.robots = a.get("content", "").lower()
            elif key.startswith(("og:", "twitter:")):
                f.meta[key] = a.get("content", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split():
            f.canonical = a.get("href", "")
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._ld = []
        elif tag == "a" and a.get("href"):
            f.links.append(a["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self.facts.title = re.sub(r"\s+", " ", "".join(self._title)).strip()
        elif tag == "script" and self._ld is not None:
            self.facts.ld_json.append("".join(self._ld))
            self._ld = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)
        elif self._ld is not None:
            self._ld.append(data)


def parse_page(body: bytes) -> PageFacts:
    p = _Parser()
    p.feed(body.decode("utf-8", errors="replace"))
    return p.facts


def norm_path(path: str) -> str:
    return path.rstrip("/") or "/"


def image_size(data: bytes) -> Optional[Tuple[int, int]]:
    """(width, height) of a PNG, JPEG or WebP from its header bytes, else None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        chunk = data[12:16]
        if chunk == b"VP8X" and len(data) >= 30:
            return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
        if chunk == b"VP8 " and len(data) >= 30:
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L" and len(data) >= 25:
            b = data[21:25]
            return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def _ld_nodes(blocks: List[str]) -> Tuple[List[dict], List[str]]:
    nodes: List[dict] = []
    errors: List[str] = []
    for raw in blocks:
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"JSON-LD does not parse ({exc.msg} at line {exc.lineno})")
            continue
        stack = doc if isinstance(doc, list) else [doc]
        while stack:
            n = stack.pop()
            if isinstance(n, dict):
                nodes.append(n)
                stack.extend(n.get("@graph") or [])
    return nodes, errors


def _types(node: dict) -> List[str]:
    t = node.get("@type")
    return [str(x) for x in (t if isinstance(t, list) else [t]) if x]


def structured_data_findings(facts: PageFacts, kind: str) -> List[str]:
    nodes, out = _ld_nodes(facts.ld_json)
    seen = {t for n in nodes for t in _types(n)}
    for n in nodes:
        for t in _types(n):
            missing = [p for p in REQUIRED_PROPS.get(t, ()) if not n.get(p)]
            if missing:
                out.append(f"{t} lacks {', '.join(missing)}")
    need = REQUIRED_TYPES.get(kind)
    if need and not (need[0] & seen):
        out.append(f"no {need[1]} JSON-LD on a {kind} page")
    return out


def social_findings(facts: PageFacts, fetch: Fetch) -> List[str]:
    out = [f"missing {k}" for k in ("og:title", "og:description", "og:url", "og:type", "og:image") if not facts.meta.get(k)]
    if facts.meta.get("twitter:card") != "summary_large_image":
        out.append(f"twitter:card is {facts.meta.get('twitter:card') or 'missing'}, not summary_large_image")
    image = facts.meta.get("og:image")
    if image:
        if not urlsplit(image).scheme:
            out.append(f"og:image {image} is not absolute")
        declared = (facts.meta.get("og:image:width"), facts.meta.get("og:image:height"))
        size: Optional[Tuple[int, int]] = None
        if all(declared) and all(d.isdigit() for d in declared):  # type: ignore[union-attr]
            size = (int(declared[0]), int(declared[1]))  # type: ignore[arg-type]
        else:
            r = fetch(urlsplit(image).path)
            size = image_size(r.body) if r.status == 200 else None
            if r.status != 200:
                out.append(f"og:image {urlsplit(image).path} answers {r.status or 'nothing'} on the preview")
        if size is not None and tuple(size) != SOCIAL_CARD:
            out.append(f"og:image is {size[0]}x{size[1]}, not {SOCIAL_CARD[0]}x{SOCIAL_CARD[1]}")
    return out


def length_findings(label: str, value: Optional[str], lo: int, hi: int, shared_with: List[str]) -> List[str]:
    if not value:
        return [f"{label} missing"]
    out = []
    if not lo <= len(value) <= hi:
        out.append(f"{label} is {len(value)} characters (want {lo} to {hi}): {value[:70]!r}")
    if shared_with:
        out.append(f"{label} shared with {', '.join(shared_with[:3])}")
    return out


def canonical_findings(facts: PageFacts, path: str) -> List[str]:
    c = facts.canonical
    if not c:
        return ["no rel=canonical"]
    parts = urlsplit(c)
    if not parts.scheme or not parts.netloc:
        return [f"canonical {c} is not absolute"]
    if norm_path(parts.path) != norm_path(path):
        return [f"canonical points to {parts.path}, not {path}"]
    return []


def resolve_internal(href: str, page_path: str, site_hosts: set) -> Optional[str]:
    """The path an internal link asks the preview for, or None for an external, mail, phone or in-page link."""
    if href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    full = urlsplit(urljoin(f"http://site{page_path}", href))
    if full.scheme not in ("http", "https") or (full.netloc != "site" and full.netloc not in site_hosts):
        return None
    return full.path or "/"


def answers_ok(fetch: Fetch, path: str, site_hosts: set) -> Tuple[bool, str]:
    """Whether a path ends at a 200 within one redirect hop; with the status trail."""
    r = fetch(path)
    if r.status in (301, 302, 303, 307, 308):
        nxt = urlsplit(r.headers.get("location", ""))
        if nxt.netloc and nxt.netloc not in site_hosts:
            return True, f"{r.status} to another host"
        r2 = fetch(nxt.path or "/")
        return r2.status == 200, f"{r.status} then {r2.status}"
    return r.status == 200, str(r.status)


def site_pages(site_dir: Optional[Path]) -> List[str]:
    """Every pretty URL a built `.html` file serves (`about.html` -> `/about`, `x/index.html` -> `/x/`), 404 page aside."""
    if site_dir is None:
        return []
    out = []
    for f in sorted(site_dir.rglob("*.html")):
        rel = f.relative_to(site_dir).as_posix()
        if rel == "404.html" or any(part.startswith(("_", ".")) for part in rel.split("/")):
            continue
        out.append("/" + rel[: -len("index.html")] if rel.endswith("index.html") else "/" + rel[: -len(".html")])
    return out


def sitemap_paths(fetch: Fetch) -> Tuple[Optional[List[str]], set, Optional[str]]:
    """`(paths, hosts, problem)` from `/sitemap.xml`."""
    r = fetch("/sitemap.xml")
    if r.status != 200:
        return None, set(), f"/sitemap.xml answers {r.status}"
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.body.decode("utf-8", errors="replace"))
    return [urlsplit(u).path or "/" for u in locs], {urlsplit(u).netloc for u in locs}, None


def robots_findings(fetch: Fetch) -> List[str]:
    r = fetch("/robots.txt")
    if r.status != 200:
        return [f"/robots.txt answers {r.status}"]
    text = r.body.decode("utf-8", errors="replace")
    out = []
    agent_star, blocked = False, False
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            agent_star = value == "*"
        elif key == "disallow" and agent_star and value == "/":
            blocked = True
    if blocked:
        out.append("User-agent: * is disallowed from /")
    if not re.search(r"^\s*sitemap:\s*\S+", text, re.I | re.M):
        out.append("no Sitemap: line")
    return out


def redirect_findings(fetch: Fetch, site_dir: Optional[Path]) -> List[str]:
    rules = (site_dir / "_redirects") if site_dir else None
    if rules is None or not rules.is_file():
        return []
    out = []
    for line in rules.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#") or len(parts) < 2 or "*" in parts[0] or ":" in parts[0]:
            continue
        src = parts[0]
        r = fetch(src)
        if r.status != 301:
            out.append(f"{src} answers {r.status}, not 301")
            continue
        dest = urlsplit(r.headers.get("location", ""))
        if dest.netloc:
            continue  # another host: its answer is not the site's to give
        r2 = fetch(dest.path or "/")
        if r2.status != 200:
            out.append(f"{src} -> {dest.path} answers {r2.status} (not one hop to a page)")
    return out


def soft_404(fetch: Fetch) -> List[str]:
    probe = f"/design-review-missing-{uuid.uuid4().hex[:8]}"
    r = fetch(probe)
    return [] if r.status == 404 else [f"{probe} answers {r.status}"]


def is_noindex(facts: PageFacts, r: Response) -> bool:
    return "noindex" in facts.robots or "noindex" in r.headers.get("x-robots-tag", "").lower()


def page_kind(path: str, facts: PageFacts, declared: Dict[str, str]) -> str:
    """The page type for structured data and budgets: declared glob first, then the home page, then its JSON-LD."""
    for pattern, kind in declared.items():
        if fnmatch(norm_path(path), norm_path(pattern)):
            return kind
    if norm_path(path) == "/":
        return "home"
    seen = {t for n in _ld_nodes(facts.ld_json)[0] for t in _types(n)}
    if "Product" in seen:
        return "product"
    if seen & {"Article", "BlogPosting", "NewsArticle", "VideoObject"}:
        return "article"
    return "landing"


def budget_type(kind: str) -> str:
    """The budget a page type is held to: the home page is a landing page."""
    return "landing" if kind == "home" else kind


def measure_site(fetch: Fetch, pages: List[str], site_dir: Optional[Path], tokens: Dict[str, str],
                 declared_types: Dict[str, str]) -> Dict[str, object]:
    """`{"site": {...}, "pages": {path: {...seo metrics, "kind": ...}}}` — every metric a list of findings."""
    lo_t, hi_t = int(tokens.get("seo.title-min", 30)), int(tokens.get("seo.title-max", 60))
    lo_d, hi_d = int(tokens.get("seo.description-min", 70)), int(tokens.get("seo.description-max", 160))
    listed, hosts, sitemap_problem = sitemap_paths(fetch)
    listed_set = {norm_path(p) for p in listed or []}
    universe = list(dict.fromkeys([norm_path(p) for p in (listed or [])][:MAX_SITEMAP_PAGES] + [norm_path(p) for p in pages]))
    facts: Dict[str, Tuple[PageFacts, Response]] = {}
    for p in universe:
        r = fetch(p)
        facts[p] = (parse_page(r.body) if r.status == 200 else PageFacts(), r)

    mismatch: List[str] = [sitemap_problem] if sitemap_problem else []
    for p in sorted(listed_set):
        f, r = facts[p] if p in facts else (PageFacts(), fetch(p))
        if r.status != 200:
            mismatch.append(f"listed {p} answers {r.status}")
        elif is_noindex(f, r):
            mismatch.append(f"listed {p} is noindex")
        elif canonical_findings(f, p) and f.canonical:
            mismatch.append(f"listed {p} declares another canonical")
    if listed is not None:
        for p in site_pages(site_dir):
            p = norm_path(p)
            if p in listed_set:
                continue
            f, r = facts[p] if p in facts else (None, fetch(p))
            if r.status != 200:
                continue
            f = f or parse_page(r.body)
            if not is_noindex(f, r) and not (f.canonical and canonical_findings(f, p)):
                mismatch.append(f"indexable {p} is missing from the sitemap")
    site = {"sitemap_mismatch": mismatch, "robots_fail": robots_findings(fetch),
            "redirect_fail": redirect_findings(fetch, site_dir), "soft_404": soft_404(fetch)}

    titles: Dict[str, List[str]] = {}
    descriptions: Dict[str, List[str]] = {}
    for p, (f, _) in facts.items():
        if f.title:
            titles.setdefault(f.title, []).append(p)
        if f.description:
            descriptions.setdefault(f.description, []).append(p)
    out: Dict[str, Dict[str, object]] = {}
    for path in pages:
        p = norm_path(path)
        f, r = facts[p]
        if r.status != 200:
            out[path] = {"error": f"{path} answers {r.status}"}
            continue
        kind = page_kind(path, f, declared_types)
        links = list(dict.fromkeys(x for x in (resolve_internal(h, path, hosts) for h in f.links) if x))[:MAX_LINKS]
        broken = []
        for link in links:
            ok, trail = answers_ok(fetch, link, hosts)
            if not ok:
                broken.append(f"{link} ({trail})")
        out[path] = {
            "kind": kind,
            "title_fail": length_findings("title", f.title, lo_t, hi_t, [x for x in titles.get(f.title or "", []) if x != p]),
            "description_fail": length_findings("description", f.description, lo_d, hi_d,
                                                [x for x in descriptions.get(f.description or "", []) if x != p]),
            "canonical_fail": canonical_findings(f, path),
            "social_card_fail": social_findings(f, fetch),
            "structured_data_fail": structured_data_findings(f, kind),
            "noindex_on_indexable": [f"noindex on a sitemap URL ({f.robots or r.headers.get('x-robots-tag')})"]
                                    if p in listed_set and is_noindex(f, r) else [],
            "broken_internal_link": broken,
        }
    return {"site": site, "pages": out}
