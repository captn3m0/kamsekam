"""Convert between the internal model and typed httpx2 requests."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import httpx2

from kamsekam.model import Body, HttpRequest, Pair, classify_body
from kamsekam.parse.curl import parse_curl

__all__ = ["from_httpx2", "to_curl", "to_httpx2", "uncurl"]


def to_httpx2(request: HttpRequest) -> httpx2.Request:
    """Render the request as an httpx2.Request, headers and body exactly as sent."""
    return httpx2.Request(
        request.method,
        httpx2.URL(request.url),
        headers=request.wire_headers(),
        content=request.body_bytes(),
    )


def from_httpx2(request: httpx2.Request) -> HttpRequest:
    """Take an httpx2.Request apart into atoms kamsekam can remove."""
    parts = urlsplit(str(request.url))
    base_url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
    query = [Pair.from_query(seg) for seg in parts.query.split("&") if seg]

    headers: list[tuple[str, str]] = []
    cookies: list[Pair] = []
    for name, value in request.headers.multi_items():
        lowered = name.lower()
        if lowered == "cookie":
            cookies.extend(Pair.from_cookie(s) for s in value.split(";") if s.strip())
        elif lowered not in ("host", "content-length"):
            headers.append((name, value))

    body: Body | None = None
    payload = request.content
    if payload:
        content_type = request.headers.get("content-type")
        body = classify_body(payload, content_type)

    return HttpRequest(
        method=request.method.upper(),
        base_url=base_url,
        query=query,
        headers=headers,
        cookies=cookies,
        body=body,
    )


def uncurl(command: str | list[str], implicit_headers: bool = True) -> httpx2.Request:
    """Parse a curl command, or an already-split argv, into a typed httpx2.Request."""
    return to_httpx2(parse_curl(command, implicit_headers=implicit_headers))


def to_curl(request: httpx2.Request | HttpRequest, impersonate: str | None = None) -> str:
    """Render an httpx2.Request, or the internal model, back into a curl command."""
    internal = request if isinstance(request, HttpRequest) else from_httpx2(request)
    return internal.to_curl(impersonate=impersonate)
