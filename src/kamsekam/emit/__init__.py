"""Render a minimised request in whichever format the caller asked for."""

from __future__ import annotations

import json as jsonlib

from kamsekam.emit.bru import to_bru
from kamsekam.emit.code import (
    to_fetch,
    to_http,
    to_httpie,
    to_httpx,
    to_powershell,
    to_requests,
    to_wget,
)
from kamsekam.emit.curl import to_curl
from kamsekam.emit.har import to_har
from kamsekam.model import HttpRequest
from kamsekam.oracle import Signature

FORMATS = (
    "curl",
    "har",
    "bru",
    "httpie",
    "requests",
    "httpx",
    "fetch",
    "wget",
    "powershell",
    "http",
    "json",
)

__all__ = ["FORMATS", "render", "to_bru", "to_curl", "to_har"]


def to_json(request: HttpRequest, signature: Signature | None = None) -> str:
    payload = request.body_bytes()
    document: dict[str, object] = {
        "method": request.method,
        "url": request.url,
        "headers": [{"name": n, "value": v} for n, v in request.headers],
        "cookies": [{"name": p.name, "value": p.value} for p in request.cookies],
        "query": [{"name": p.name, "value": p.value} for p in request.query],
        "body": payload.decode("utf-8", "replace") if payload else None,
    }
    if signature is not None:
        document["signature"] = {
            "status": signature.status,
            "content_type": signature.content_type,
            "size": signature.size,
            "sha256": signature.digest,
        }
    return jsonlib.dumps(document, indent=2)


def render(
    fmt: str,
    request: HttpRequest,
    signature: Signature | None = None,
    impersonate: str | None = None,
    wrap: bool = False,
) -> str:
    if fmt == "curl":
        return to_curl(request, impersonate=impersonate, wrap=wrap)
    if fmt == "har":
        return to_har(request, signature=signature)
    if fmt == "bru":
        return to_bru(request)
    if fmt == "httpie":
        return to_httpie(request)
    if fmt == "requests":
        return to_requests(request)
    if fmt == "httpx":
        return to_httpx(request)
    if fmt == "fetch":
        return to_fetch(request)
    if fmt == "wget":
        return to_wget(request)
    if fmt == "powershell":
        return to_powershell(request)
    if fmt == "http":
        return to_http(request)
    if fmt == "json":
        return to_json(request, signature=signature)
    raise ValueError(f"unknown format {fmt!r}, pick from {', '.join(FORMATS)}")
