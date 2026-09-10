"""Render an HttpRequest into the HAR entry shape, and into a whole archive."""

from __future__ import annotations

import json as jsonlib
from datetime import datetime, timezone
from typing import Any

from kamsekam.model import HttpRequest
from kamsekam.oracle import Signature

HAR_VERSION = "1.2"


def har_request_dict(request: HttpRequest, http_version: str = "HTTP/1.1") -> dict[str, Any]:
    """Serialise into the HAR request shape, ready to drop into an entry."""
    payload = request.body_bytes()
    document: dict[str, Any] = {
        "method": request.method,
        "url": request.url,
        "httpVersion": http_version,
        "cookies": [{"name": p.name, "value": p.value} for p in request.cookies],
        "headers": [{"name": n, "value": v} for n, v in request.wire_headers()],
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


def to_har(request: HttpRequest, signature: Signature | None = None) -> str:
    from kamsekam import __version__

    size = signature.size if signature else 0
    entry = {
        "startedDateTime": datetime.now(timezone.utc).isoformat(),
        "time": 0,
        "request": har_request_dict(request),
        "response": {
            "status": signature.status if signature else 0,
            "statusText": "",
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": [],
            "content": {"size": size, "mimeType": signature.content_type if signature else ""},
            "redirectURL": "",
            "headersSize": -1,
            "bodySize": size,
        },
        "cache": {},
        "timings": {"send": 0, "wait": 0, "receive": 0},
    }
    log = {
        "version": HAR_VERSION,
        "creator": {"name": "kamsekam", "version": __version__},
        "entries": [entry],
    }
    return jsonlib.dumps({"log": log}, indent=2)
