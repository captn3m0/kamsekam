"""Minimise every GET entry in a HAR archive, leaving the rest of it intact."""

from __future__ import annotations

import json as jsonlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minreq.minimizer import Result
from minreq.model import HttpRequest
from minreq.parse.har import parse_har

ARCHIVE_METHODS = ("GET",)


@dataclass
class EntryOutcome:
    index: int
    method: str
    url: str
    state: str
    result: Result | None = None
    detail: str = ""


def derive_output_path(source: str | Path, suffix: str = "-minimal") -> Path:
    """file.har -> file-minimal.har, then file-minimal-1.har, -2, ... if taken."""
    path = Path(source).expanduser()
    stem = path.stem if path.suffix.lower() == ".har" else path.name
    candidate = path.with_name(f"{stem}{suffix}.har")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{stem}{suffix}-{counter}.har")
        counter += 1
    return candidate


def har_request_dict(request: HttpRequest, http_version: str = "HTTP/1.1") -> dict[str, Any]:
    """Serialise back into the HAR request shape, ready to drop into an entry."""
    payload = request.body_bytes()
    headers = [{"name": name, "value": value} for name, value in request.wire_headers()]
    document: dict[str, Any] = {
        "method": request.method,
        "url": request.url,
        "httpVersion": http_version,
        "cookies": [{"name": p.name, "value": p.value} for p in request.cookies],
        "headers": headers,
        "queryString": [{"name": p.name, "value": p.value} for p in request.query],
        "headersSize": -1,
        "bodySize": len(payload) if payload else 0,
    }
    if payload is not None:
        document["postData"] = {
            "mimeType": request.header_value("content-type") or "application/octet-stream",
            "text": payload.decode("utf-8", "replace"),
        }
    return document


def load_document(text: str) -> dict[str, Any]:
    document: Any = jsonlib.loads(text)
    if not isinstance(document, dict) or "entries" not in document.get("log", {}):
        raise ValueError("not a HAR archive: no log.entries")
    return document


def minimise_document(
    document: dict[str, Any],
    minimise_entry: Callable[[HttpRequest, int], Result],
    only: int | str | None = None,
    methods: tuple[str, ...] = ARCHIVE_METHODS,
) -> tuple[dict[str, Any], list[EntryOutcome]]:
    """Replace the request of every eligible entry with its minimised version."""
    entries = document["log"]["entries"]
    outcomes: list[EntryOutcome] = []
    for index, entry in enumerate(entries):
        raw = entry.get("request", {})
        method = (raw.get("method") or "GET").upper()
        url = raw.get("url", "")
        if not _selected(index, url, only):
            outcomes.append(EntryOutcome(index, method, url, "skipped", detail="not selected"))
            continue
        if method not in methods:
            outcomes.append(EntryOutcome(index, method, url, "skipped", detail=f"{method} request"))
            continue
        try:
            request = parse_har(document, entry=index)
        except ValueError as exc:
            outcomes.append(EntryOutcome(index, method, url, "failed", detail=str(exc)))
            continue
        try:
            result = minimise_entry(request, index)
        except Exception as exc:  # noqa: BLE001
            outcomes.append(EntryOutcome(index, method, url, "failed", detail=str(exc)))
            continue
        entry["request"] = har_request_dict(
            result.request, http_version=raw.get("httpVersion", "HTTP/1.1")
        )
        outcomes.append(EntryOutcome(index, method, url, "minimised", result=result))
    return document, outcomes


def _selected(index: int, url: str, only: int | str | None) -> bool:
    if only is None:
        return True
    if isinstance(only, int):
        return index == only
    return only in url
