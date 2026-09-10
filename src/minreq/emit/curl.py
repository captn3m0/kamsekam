"""Render an HttpRequest back into a curl command line."""

from __future__ import annotations

import re

from minreq.model import HttpRequest
from minreq.parse.curl import COMPRESSED_ENCODINGS

_SAFE = re.compile(r"^[A-Za-z0-9_@%+=:,./^-]+$")
_COMPRESSION_TOKENS = {"gzip", "deflate", "br", "zstd", "compress"}


def quote_always(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def quote(text: str) -> str:
    if text and _SAFE.match(text):
        return text
    return quote_always(text)


def _wants_compression(value: str) -> bool:
    tokens = {t.strip().split(";")[0].lower() for t in value.split(",")}
    return bool(tokens & _COMPRESSION_TOKENS)


def to_curl(request: HttpRequest, impersonate: str | None = None, wrap: bool = False) -> str:
    parts: list[str] = ["curl"]
    if request.method != "GET" or (request.method == "GET" and request.body_bytes()):
        parts += ["-X", request.method]
    parts.append(quote_always(request.url))

    compressed = False
    for name, value in request.headers:
        if name.lower() == "accept-encoding" and _wants_compression(value):
            compressed = True
            if value == COMPRESSED_ENCODINGS:
                continue
        parts += ["-H", quote_always(f"{name}: {value}")]

    if request.cookies:
        parts += ["-b", quote_always("; ".join(p.raw for p in request.cookies))]

    body = request.body
    if body is not None and not body.is_empty():
        if body.kind == "multipart":
            for pair in body.fields:
                parts += ["-F", quote_always(f"{pair.name}={pair.value}")]
        else:
            parts += ["--data-raw", quote_always(body.text())]
        if request.header_value("content-type") is None and body.kind != "multipart":
            parts += ["-H", quote_always("Content-Type:")]

    if compressed:
        parts.append("--compressed")
    if request.follow_redirects:
        parts.append("-L")
    if request.insecure:
        parts.append("-k")
    if request.http_version:
        parts.append(f"--{request.http_version}")
    if request.proxy:
        parts += ["-x", quote(request.proxy)]
    if impersonate:
        parts += ["--impersonate", quote(impersonate)]

    if not wrap:
        return " ".join(parts)
    return _wrap(parts)


def _wrap(parts: list[str]) -> str:
    lines: list[str] = []
    buf: list[str] = []
    for token in parts:
        if token in ("-H", "-b", "-F", "-X", "--data-raw", "-x", "--impersonate") and buf:
            lines.append(" ".join(buf))
            buf = [token]
        else:
            buf.append(token)
    if buf:
        lines.append(" ".join(buf))
    return " \\\n  ".join(lines)
