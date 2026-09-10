"""Render an HttpRequest as an httpie command or as raw HTTP/1.1."""

from __future__ import annotations

import json as jsonlib
from urllib.parse import urlsplit

from kamsekam.emit.curl import quote
from kamsekam.model import HttpRequest


def to_httpie(request: HttpRequest) -> str:
    parts = ["http"]
    if request.follow_redirects:
        parts.append("--follow")
    if request.insecure:
        parts.append("--verify=no")
    payload = request.body_bytes()
    body = request.body
    if payload is not None and (body is None or body.kind != "json"):
        parts.append("--raw " + quote(payload.decode("utf-8", "replace")))
    parts += [request.method, quote(request.url)]
    for name, value in request.headers:
        parts.append(quote(f"{name}:{value}"))
    if request.cookies:
        parts.append(quote("Cookie:" + "; ".join(p.raw for p in request.cookies)))
    if body is not None and body.kind == "json" and isinstance(body.data, dict):
        for key, value in body.data.items():
            operator = "=" if isinstance(value, str) else ":="
            rendered = value if isinstance(value, str) else jsonlib.dumps(value)
            parts.append(quote(f"{key}{operator}{rendered}"))
    return " ".join(parts)


def to_http(request: HttpRequest) -> str:
    parts = urlsplit(request.base_url)
    target = request.url[len(f"{parts.scheme}://{parts.netloc}") :] or "/"
    lines = [f"{request.method} {target} HTTP/1.1", f"Host: {parts.netloc}"]
    lines += [f"{name}: {value}" for name, value in request.wire_headers()]
    payload = request.body_bytes()
    if payload is not None:
        lines.append(f"Content-Length: {len(payload)}")
    text = "\r\n".join(lines) + "\r\n\r\n"
    if payload is not None:
        text += payload.decode("utf-8", "replace")
    return text
