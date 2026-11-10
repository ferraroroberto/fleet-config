"""Read RSS 2.0 and Atom feeds (newsletters, YouTube channels) for /inspiration-scan.

Read-only GETs, one per source per run. Each source reports its own state:
`ok` with its items, or `not-read` with a reason. A feed that could not be
fetched or parsed is never reported as "nothing new".

stdlib only.
"""

from __future__ import annotations

import datetime as _dt
import email.utils
import hashlib
import html
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from sources import Source

STATUS_OK = "ok"
STATUS_NOT_READ = "not-read"
STATUS_UNKNOWN = "unknown"
# X was skipped because the dedicated Chrome profile is not logged in: the owner's step, not a quiet day.
STATUS_LOGIN_NEEDED = "login-needed"

# A feed answers in well under a second; twenty covers a slow host without
# letting one dead source stall the whole run.
FETCH_TIMEOUT_S = 20
# Enough of an item's text to judge relevance; the hub prompt stays small.
TEXT_LIMIT = 500
USER_AGENT = "inspiration-scan/1.0 (personal feed reader)"

_ATOM = "{http://www.w3.org/2005/Atom}"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


@dataclass
class Item:
    id: str
    kind: str
    source: str
    title: str
    link: str
    text: str
    published: Optional[_dt.datetime]
    author: str = ""


@dataclass
class SourceResult:
    source: Source
    status: str
    reason: str = ""
    items: List[Item] = field(default_factory=list)


def item_id(link: str) -> str:
    """Stable id for cross-day dedupe: the link without query noise or fragment."""
    normalized = link.strip().split("#", 1)[0]
    normalized = re.sub(r"[?&]utm_[^&]*", "", normalized).rstrip("?&/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def clean_text(raw: str) -> str:
    text = _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", raw or ""))).strip()
    return text[:TEXT_LIMIT]


def parse_date(raw: Optional[str]) -> Optional[_dt.datetime]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_dt.timezone.utc)


def _text(node: Optional[ET.Element]) -> str:
    return (node.text or "") if node is not None else ""


def _rss_items(root: ET.Element, source: Source) -> List[Item]:
    items = []
    for entry in root.iter("item"):
        link = _text(entry.find("link")).strip() or _text(entry.find("guid")).strip()
        if not link:
            continue
        items.append(Item(item_id(link), source.kind, source.name, clean_text(_text(entry.find("title"))),
                          link, clean_text(_text(entry.find("description"))),
                          parse_date(_text(entry.find("pubDate"))),
                          clean_text(_text(entry.find(f"{_DC}creator")) or _text(entry.find("author")))))
    return items


def _atom_link(entry: ET.Element) -> str:
    for link in entry.findall(f"{_ATOM}link"):
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href", "")
    return ""


def _atom_items(root: ET.Element, source: Source) -> List[Item]:
    items = []
    for entry in root.iter(f"{_ATOM}entry"):
        link = _atom_link(entry)
        if not link:
            continue
        summary = (_text(entry.find(f"{_ATOM}summary")) or _text(entry.find(f"{_ATOM}content"))
                   or _text(entry.find(f"{_MEDIA}group/{_MEDIA}description")))
        published = _text(entry.find(f"{_ATOM}published")) or _text(entry.find(f"{_ATOM}updated"))
        items.append(Item(item_id(link), source.kind, source.name, clean_text(_text(entry.find(f"{_ATOM}title"))),
                          link, clean_text(summary), parse_date(published),
                          clean_text(_text(entry.find(f"{_ATOM}author/{_ATOM}name")))))
    return items


def parse_feed(body: bytes, source: Source) -> List[Item]:
    """Items from an RSS 2.0 or Atom document; `ET.ParseError` when it is neither."""
    root = ET.fromstring(body)  # noqa: S314 - stdlib expat resolves no external entities and caps entity expansion
    if root.tag == f"{_ATOM}feed":
        return _atom_items(root, source)
    if root.tag == "rss" or root.find("channel") is not None:
        return _rss_items(root, source)
    raise ET.ParseError(f"not an RSS or Atom document (root <{root.tag}>)")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - http(s) checked by read_source
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:  # noqa: S310 - http(s) feed URLs from the owner's list
        return response.read()


def read_source(source: Source, since: _dt.datetime,
                fetcher: Optional[Callable[[str], bytes]] = None) -> SourceResult:
    """Fetch one feed and keep items published at or after `since`."""
    if not source.url.startswith(("https://", "http://")):
        return SourceResult(source, STATUS_NOT_READ, "feed URL is not http(s)")
    try:
        body = (fetcher or fetch)(source.url)
    except urllib.error.HTTPError as exc:
        return SourceResult(source, STATUS_NOT_READ, f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return SourceResult(source, STATUS_NOT_READ, f"fetch failed: {type(exc).__name__}")
    try:
        items = parse_feed(body, source)
    except ET.ParseError:
        return SourceResult(source, STATUS_NOT_READ, "not a readable RSS/Atom feed")
    recent = [i for i in items if i.published is None or i.published >= since]
    return SourceResult(source, STATUS_OK, items=recent)
