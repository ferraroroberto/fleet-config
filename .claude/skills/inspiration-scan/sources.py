"""Source list + state paths for /inspiration-scan.

The source list names the people and publications the owner follows, so it is
private, machine-local data: it lives in the state root, never in the repo. The
committed `sources.example.toml` beside this file documents the shape with
placeholder names only.

stdlib only.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

STATE_ROOT_ENV_VAR = "INSPIRATION_SCAN_ROOT"
SOURCES_FILENAME = "sources.toml"

KIND_NEWSLETTER = "newsletter"
KIND_YOUTUBE = "youtube"
KIND_X = "x"

YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

# A digest of more than about eight ideas stops being read on a phone.
DEFAULT_TOP_N = 8
# A week covers a missed run or two without flooding the first run with a
# feed's whole archive.
DEFAULT_LOOKBACK_DAYS = 7
# Bounds one run's hub cost if a source suddenly publishes a burst.
DEFAULT_MAX_SCORED = 120
DEFAULT_MODEL = "claude-haiku-5-5"
# Three scrolls of a profile page reach roughly a day or two of posts for an
# active account, which is all one daily pass needs.
DEFAULT_X_SCROLLS = 3


@dataclass(frozen=True)
class Source:
    kind: str
    name: str
    url: str  # the feed URL, or the X handle for kind "x"


@dataclass
class Settings:
    top_n: int = DEFAULT_TOP_N
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    max_scored: int = DEFAULT_MAX_SCORED
    model: str = DEFAULT_MODEL
    x_profile_dir: str = ""
    x_python: str = ""
    x_scrolls: int = DEFAULT_X_SCROLLS


@dataclass
class Config:
    likes: str = ""
    sources: List[Source] = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)


class ConfigError(ValueError):
    """The source list exists but cannot be used as written."""


def state_root() -> Path:
    """Machine-local home of the source list, the seen ledger and the digests."""
    root = os.environ.get(STATE_ROOT_ENV_VAR)
    return Path(root) if root else Path.home() / ".claude" / "inspiration-scan"


def sources_path() -> Path:
    return state_root() / SOURCES_FILENAME


def _likes_text(raw: str, base: Path) -> str:
    """`likes` is either the note itself or a path to a file holding it."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    if raw and len(raw) < 260 and candidate.suffix in {".md", ".txt"} and candidate.is_file():
        return candidate.read_text(encoding="utf-8", errors="replace").strip()
    return raw.strip()


def _entries(data: dict, key: str) -> list:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ConfigError(f"[[{key}]] must be a list of tables")
    return value


def _required(entry: dict, key: str, table: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[[{table}]] entry is missing {key!r}")
    return value.strip()


def parse_config(data: dict, base: Path) -> Config:
    """Validate a parsed TOML document into a `Config`."""
    sources: List[Source] = []
    for entry in _entries(data, KIND_NEWSLETTER):
        sources.append(Source(KIND_NEWSLETTER, _required(entry, "name", KIND_NEWSLETTER),
                              _required(entry, "feed", KIND_NEWSLETTER)))
    for entry in _entries(data, KIND_YOUTUBE):
        channel = _required(entry, "channel_id", KIND_YOUTUBE)
        sources.append(Source(KIND_YOUTUBE, _required(entry, "name", KIND_YOUTUBE),
                              YOUTUBE_FEED.format(channel_id=channel)))
    for entry in _entries(data, KIND_X):
        handle = _required(entry, "handle", KIND_X).lstrip("@")
        sources.append(Source(KIND_X, f"@{handle}", handle))

    raw_settings = data.get("settings", {})
    if not isinstance(raw_settings, dict):
        raise ConfigError("[settings] must be a table")
    settings = Settings()
    for name in ("top_n", "lookback_days", "max_scored", "x_scrolls"):
        if name in raw_settings:
            value = raw_settings[name]
            if not isinstance(value, int) or value < 1:
                raise ConfigError(f"settings.{name} must be a positive integer")
            setattr(settings, name, value)
    for name in ("model", "x_profile_dir", "x_python"):
        if name in raw_settings:
            setattr(settings, name, str(raw_settings[name]).strip())

    profile = data.get("profile", {})
    likes = _likes_text(str(profile.get("likes", "")), base) if isinstance(profile, dict) else ""
    return Config(likes=likes, sources=sources, settings=settings)


def load_config(path: Optional[Path] = None) -> Config:
    """Read the source list; `FileNotFoundError` when it has not been created yet."""
    target = path or sources_path()
    with target.open("rb") as handle:
        try:
            data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{target.name} is not valid TOML: {exc}") from exc
    return parse_config(data, target.parent)
