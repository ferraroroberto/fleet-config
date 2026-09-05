"""Versioned native-quota observations and atomic, per-producer storage.

No tokens, model catalogs or credentials enter this contract. See
docs/quota-snapshots.md for the consumer and account-identity boundaries.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 1
MAX_AGE_SECONDS = 600
STATES = {"available", "stale", "unknown", "unsupported", "error"}
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}\Z")
OPAQUE_KEY = re.compile(r"sha256:[0-9a-f]{64}\Z")
DEFAULT_SOURCES = {"claude-statusline": ("claude", "anthropic"),
                   "codex-app-server": ("codex", "openai")}


def utc_now() -> dt.datetime:
    """Return an aware UTC time."""
    return dt.datetime.now(dt.timezone.utc)


def iso_utc(value: dt.datetime) -> str:
    """Serialize an aware time as UTC, never assume a naive clock's zone."""
    if value.tzinfo is None:
        raise ValueError("timezone_required")
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_time(value: Any) -> Optional[dt.datetime]:
    """Accept an epoch or timezone-bearing ISO timestamp, rejecting bool/NaN."""
    try:
        if type(value) in (int, float) and math.isfinite(value):
            return dt.datetime.fromtimestamp(value, dt.timezone.utc)
        if isinstance(value, str):
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(dt.timezone.utc)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def identifier(value: Any) -> str:
    """Validate a non-secret protocol identifier used in keys and filenames."""
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("invalid_identifier")
    return value


def account_key(provider: str, native_id: str) -> str:
    """One-way account identifier; never hash credentials or infer from email."""
    if not isinstance(native_id, str) or not native_id.strip():
        raise ValueError("missing_account_identity")
    return "sha256:" + hashlib.sha256(
        (identifier(provider) + "\0" + native_id).encode("utf-8")
    ).hexdigest()


def pool_key(provider: str, account: Optional[str], bucket: str) -> Optional[str]:
    """Only identified provider/account/bucket scopes can name a shared pool."""
    if account is None:
        return None
    if not OPAQUE_KEY.fullmatch(account):
        raise ValueError("invalid_account_key")
    return "sha256:" + hashlib.sha256(
        "\0".join((identifier(provider), account, identifier(bucket))).encode("utf-8")
    ).hexdigest()


def empty_source(producer: str, harness: str, provider: str, state: str,
                 reason: str, *, now: Optional[dt.datetime] = None,
                 client_version: Optional[str] = None) -> dict[str, Any]:
    """Represent a failed/absent/future adapter without inventing any capacity."""
    if state not in STATES - {"available", "stale"}:
        raise ValueError("invalid_empty_state")
    return {"schema_version": SCHEMA_VERSION, "producer": identifier(producer),
            "harness": identifier(harness), "provider": identifier(provider),
            "state": state, "reason": identifier(reason),
            "checked_at": iso_utc(now or utc_now()),
            "source": {"kind": identifier(producer), "adapter_version": 1,
                       "client_version": client_version},
            "observations": []}


def window(name: str, duration: Any, used: Any, resets: Any) -> dict[str, Any]:
    """Normalize one native window; missing utilization never becomes zero."""
    valid_duration = type(duration) is int and duration > 0
    valid_used = type(used) in (int, float) and math.isfinite(used) and 0 <= used <= 100
    reset = parse_time(resets)
    valid_reset = resets is None or reset is not None
    return {"id": identifier(name), "duration_minutes": duration if valid_duration else None,
            "used_percentage": float(used) if valid_used else None,
            "resets_at": iso_utc(reset) if reset is not None else None,
            "state": "available" if valid_duration and valid_used and valid_reset else "unknown"}


def observation(provider: str, bucket: str, windows: list[dict[str, Any]],
                *, now: dt.datetime, account: Optional[str] = None) -> dict[str, Any]:
    """Build a measurement, keeping account resolution separate from usage."""
    return {"bucket": identifier(bucket),
            "account": {"key": account, "state": "identified" if account else "unknown"},
            "pool_id": pool_key(provider, account, bucket),
            "observed_at": iso_utc(now),
            "expires_at": iso_utc(now + dt.timedelta(seconds=MAX_AGE_SECONDS)),
            "state": "available" if windows and all(w["state"] == "available" for w in windows) else "unknown",
            "windows": windows}


def validate_source(raw: Any) -> dict[str, Any]:
    """Reject incompatible disk contracts and strip all uncontracted fields."""
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_version")
    producer, harness, provider = (identifier(raw[k]) for k in ("producer", "harness", "provider"))
    state, reason = raw["state"], identifier(raw["reason"])
    if state not in STATES:
        raise ValueError("invalid_state")
    checked = parse_time(raw["checked_at"])
    source = raw["source"]
    if checked is None or not isinstance(source, dict) or source["kind"] != producer or type(source["adapter_version"]) is not int or source["adapter_version"] != 1:
        raise ValueError("source_version")
    client = source.get("client_version")
    if client is not None and (not isinstance(client, str) or re.fullmatch(r"\d+\.\d+\.\d+", client) is None):
        raise ValueError("client_version")
    result = empty_source(producer, harness, provider, "unknown", reason,
                          now=checked, client_version=client)
    result["state"] = state
    observations = raw["observations"]
    if not isinstance(observations, list) or len(observations) > 100:
        raise ValueError("invalid_observations")
    buckets = set()
    for item in observations:
        bucket = identifier(item["bucket"])
        if bucket in buckets:
            raise ValueError("duplicate_bucket")
        buckets.add(bucket)
        account = item["account"]
        key = account["key"]
        if account["state"] != ("identified" if key else "unknown"):
            raise ValueError("invalid_account_state")
        expected_pool = pool_key(provider, key, bucket)
        if item["pool_id"] != expected_pool:
            raise ValueError("invalid_pool_id")
        observed, expires = parse_time(item["observed_at"]), parse_time(item["expires_at"])
        if observed is None or expires is None or observed > checked or not 0 < (expires - observed).total_seconds() <= MAX_AGE_SECONDS:
            raise ValueError("invalid_observation_time")
        windows = item["windows"]
        if not isinstance(windows, list) or len(windows) > 100:
            raise ValueError("invalid_windows")
        normalized = []
        names = set()
        for entry in windows:
            name = identifier(entry["id"])
            if name in names:
                raise ValueError("duplicate_window")
            names.add(name)
            rebuilt = window(name, entry["duration_minutes"], entry["used_percentage"], entry["resets_at"])
            # Disk claims must agree with the validity of the actual values.
            if entry["state"] not in {"available", "unknown", "stale"} or (entry["state"] == "available" and rebuilt["state"] != "available"):
                raise ValueError("invalid_window_state")
            rebuilt["state"] = entry["state"]
            normalized.append(rebuilt)
        rebuilt_item = observation(provider, bucket, normalized, now=observed, account=key)
        if item["state"] not in {"available", "unknown", "stale"} or (item["state"] == "available" and rebuilt_item["state"] != "available"):
            raise ValueError("invalid_observation_state")
        rebuilt_item.update(state=item["state"], expires_at=iso_utc(expires))
        result["observations"].append(rebuilt_item)
    if state == "available" and (not observations or not any(o["state"] == "available" for o in result["observations"])):
        raise ValueError("empty_available")
    if state != "available" and any(o["state"] == "available" for o in result["observations"]):
        raise ValueError("source_state_mismatch")
    if state in {"error", "unsupported"} and observations:
        raise ValueError("error_with_measurements")
    return result


def state_dir() -> Path:
    """Use the same state override as the legacy Claude cache."""
    override = os.environ.get("CLAUDE_HOOKS_STATE_DIR")
    return Path(override) if override else Path.home() / ".claude" / "hooks" / "state"


def publish(raw: dict[str, Any], directory: Optional[Path] = None) -> Path:
    """Atomically replace only this producer's shard; never merge shared JSON.

    Unique temp names + os.replace permit concurrent producers and readers.
    A failed replacement retains the complete old shard and removes our temp.
    """
    source = validate_source(raw)
    folder = (directory or state_dir()) / "quota-v1"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (source["producer"] + ".json")
    fd, temporary = tempfile.mkstemp(prefix=target.name + ".tmp.", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(source, stream, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers can briefly deny replacement (native concurrent-I/O
        # probe, #751). Retry only that sharing/access condition, bounded <1s.
        for attempt in range(6):
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def refresh_states(source: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    """Compute freshness at read time; file mtimes never refresh observations."""
    result = copy.deepcopy(source)
    checked = parse_time(result["checked_at"])
    if checked is None or checked > now:
        result.update(state="unknown", reason="future_timestamp", observations=[])
        return result
    for item in result["observations"]:
        observed, expires = parse_time(item["observed_at"]), parse_time(item["expires_at"])
        expired = observed is None or expires is None or now >= expires
        for entry in item["windows"]:
            reset = parse_time(entry["resets_at"])
            if entry["state"] == "available" and (expired or (reset is not None and now >= reset)):
                entry["state"] = "stale"
        if item["state"] == "available" and (expired or any(w["state"] == "stale" for w in item["windows"])):
            item["state"] = "stale"
    if result["state"] == "available" and not any(o["state"] == "available" for o in result["observations"]):
        result.update(state="stale", reason="observation_expired")
    return result


def read_snapshot(directory: Optional[Path] = None, *, now: Optional[dt.datetime] = None) -> dict[str, Any]:
    """Read sources and deduplicate identified pools without adding percentages.

    Unbound account observations stay only in sources. Even identified buckets
    may overlap: consumers must never sum percentages or infer model access.
    """
    now = now or utc_now()
    folder = (directory or state_dir()) / "quota-v1"
    sources = []
    paths = {name: folder / (name + ".json") for name in DEFAULT_SOURCES}
    try:
        paths.update({p.stem: p for p in folder.glob("*.json") if IDENTIFIER.fullmatch(p.stem)})
    except OSError:
        pass
    for producer, path in sorted(paths.items()):
        harness, provider = DEFAULT_SOURCES.get(producer, ("unknown", "unknown"))
        try:
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("oversized_source")
            raw = json.loads(path.read_text(encoding="utf-8"))
            source = validate_source(raw)
            if source["producer"] != producer:
                raise ValueError("producer_mismatch")
            if producer in DEFAULT_SOURCES and (source["harness"], source["provider"]) != (harness, provider):
                raise ValueError("provider_mismatch")
            sources.append(refresh_states(source, now))
        except FileNotFoundError:
            sources.append(empty_source(producer, harness, provider, "unknown", "source_absent", now=now))
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            sources.append(empty_source(producer, harness, provider, "error", "source_unreadable", now=now))
    pools: dict[str, Any] = {}
    for source in sources:
        for item in source["observations"]:
            key = item["pool_id"]
            if key is None:
                continue
            existing = pools.get(key)
            contributors = sorted(set((existing or {}).get("harnesses", []) + [source["harness"]]))
            if existing is None or item["observed_at"] > existing["observed_at"]:
                pools[key] = dict(item, provider=source["provider"], producer=source["producer"],
                                  source=source["source"], harnesses=contributors)
            else:
                existing["harnesses"] = contributors
    return {"schema_version": SCHEMA_VERSION, "read_at": iso_utc(now),
            "sources": sources, "pools": list(pools.values())}
