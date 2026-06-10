import json
import ipaddress
import re
from typing import Any, Iterable, Optional
from urllib.parse import urlparse, urlunparse


def _valid_hostname(hostname: str) -> bool:
    hostname = hostname.rstrip(".")
    if not hostname or not any(char.isalnum() for char in hostname):
        return False
    if hostname.lower() == "localhost":
        return True
    if ":" in hostname:
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            return False
        return True
    if "." not in hostname:
        return False
    labels = hostname.split(".")
    return all(
        bool(re.fullmatch(r"(?!-)[A-Za-z0-9-]{1,63}(?<!-)", label))
        for label in labels
    )


def normalize_match_url(raw_url: Optional[object]) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return ""

    url = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", url)
    if not url or re.search(r"[<>\s]", url):
        return ""

    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
        return ""
    if not parsed.scheme:
        parsed = urlparse(f"https://{url}")

    hostname = parsed.hostname or ""
    if not _valid_hostname(hostname):
        return ""
    if parsed.username or parsed.password:
        return ""

    try:
        parsed.port
    except ValueError:
        return ""

    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc,
        parsed.path or "",
        parsed.params or "",
        parsed.query or "",
        parsed.fragment or "",
    ))


def normalize_bookmaker_links(
    raw_links: Any,
    *,
    allowed_bookmaker_ids: Optional[Iterable[int]] = None,
) -> list[dict[str, Any]]:
    allowed_ids = {
        int(bookmaker_id)
        for bookmaker_id in (allowed_bookmaker_ids or [])
        if bookmaker_id
    }
    values = raw_links if isinstance(raw_links, list) else [raw_links]
    normalized_by_id: dict[int, dict[str, Any]] = {}

    def add_candidate(candidate: Any) -> None:
        if hasattr(candidate, "model_dump"):
            candidate = candidate.model_dump()
        elif not isinstance(candidate, dict) and hasattr(candidate, "bookmaker_id") and hasattr(candidate, "url"):
            candidate = {
                "bookmaker_id": getattr(candidate, "bookmaker_id", None),
                "url": getattr(candidate, "url", None),
            }
        if not isinstance(candidate, dict):
            return
        raw_id = candidate.get("bookmaker_id") or candidate.get("bookmakerId") or candidate.get("id")
        raw_url = candidate.get("url") or candidate.get("link") or candidate.get("match_link")
        try:
            bookmaker_id = int(raw_id)
        except (TypeError, ValueError):
            return
        if allowed_ids and bookmaker_id not in allowed_ids:
            return
        url = normalize_match_url(raw_url)
        if not url:
            return
        normalized_by_id[bookmaker_id] = {
            "bookmaker_id": bookmaker_id,
            "url": url,
        }

    for value in values:
        if value is None:
            continue
        parsed_value: Any = value
        if isinstance(value, str):
            clean_value = value.strip()
            if not clean_value:
                continue
            try:
                parsed_value = json.loads(clean_value)
            except json.JSONDecodeError:
                continue
        if isinstance(parsed_value, list):
            for item in parsed_value:
                add_candidate(item)
        elif isinstance(parsed_value, dict):
            if any(key in parsed_value for key in ("bookmaker_id", "bookmakerId", "id")):
                add_candidate(parsed_value)
            else:
                for bookmaker_id, url in parsed_value.items():
                    add_candidate({"bookmaker_id": bookmaker_id, "url": url})
        else:
            add_candidate(parsed_value)

    return list(normalized_by_id.values())
