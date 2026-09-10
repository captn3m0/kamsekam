"""Parse a Bruno .bru request file into an HttpRequest."""

from __future__ import annotations

import json as jsonlib
import re
from urllib.parse import urlsplit, urlunsplit

from kamsekam.model import Body, HttpRequest, Pair

METHODS = ("get", "post", "put", "delete", "patch", "head", "options", "trace")
_BLOCK = re.compile(r"^([A-Za-z0-9:_-]+)\s*\{$")


class BruParseError(ValueError):
    pass


def _blocks(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _BLOCK.match(lines[i].strip())
        if not match:
            i += 1
            continue
        name = match.group(1)
        depth = 1
        body: list[str] = []
        i += 1
        while i < len(lines) and depth:
            stripped = lines[i].strip()
            depth += stripped.count("{") - stripped.count("}")
            if depth:
                body.append(lines[i])
            i += 1
        out[name] = "\n".join(body)
    return out


def _entries(block: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("~") or ":" not in stripped:
            continue
        name, _, value = stripped.partition(":")
        out.append((name.strip(), value.strip()))
    return out


def parse_bru(text: str) -> HttpRequest:
    blocks = _blocks(text)
    method = next((m for m in METHODS if m in blocks), None)
    if method is None:
        raise BruParseError("no HTTP method block (get/post/...) found in .bru file")
    settings = dict(_entries(blocks[method]))
    raw_url = settings.get("url", "")
    if not raw_url:
        raise BruParseError("method block has no url")

    parts = urlsplit(raw_url)
    base_url = urlunsplit((parts.scheme or "https", parts.netloc, parts.path or "/", "", ""))
    query = [Pair.from_query(seg) for seg in parts.query.split("&") if seg]
    for name, value in _entries(blocks.get("params:query", "")):
        query.append(Pair.encoded(name, value))

    headers: list[tuple[str, str]] = []
    cookies: list[Pair] = []
    for name, value in _entries(blocks.get("headers", "")):
        if name.lower() == "cookie":
            cookies.extend(Pair.from_cookie(s) for s in value.split(";") if s.strip())
        else:
            headers.append((name, value))

    auth = dict(_entries(blocks.get("auth:bearer", "")))
    if auth.get("token"):
        headers.append(("Authorization", f"Bearer {auth['token']}"))

    return HttpRequest(
        method=method.upper(),
        base_url=base_url,
        query=query,
        headers=headers,
        cookies=cookies,
        body=_body(blocks, settings.get("body", "none")),
    )


def _body(blocks: dict[str, str], declared: str) -> Body | None:
    if "body:json" in blocks:
        text = blocks["body:json"].strip()
        try:
            return Body(kind="json", data=jsonlib.loads(text), raw=text.encode())
        except ValueError:
            return Body(kind="raw", raw=text.encode())
    if "body:form-urlencoded" in blocks:
        fields = [Pair.encoded(n, v) for n, v in _entries(blocks["body:form-urlencoded"])]
        return Body(kind="form", fields=fields)
    if "body:multipart-form" in blocks:
        fields = [Pair(f"{n}={v}", n, v) for n, v in _entries(blocks["body:multipart-form"])]
        return Body(kind="multipart", fields=fields)
    for key in ("body:text", "body:xml", "body:sparql", "body:graphql"):
        if key in blocks:
            return Body(kind="raw", raw=blocks[key].strip().encode())
    if declared not in ("none", "") and "body" in blocks:
        return Body(kind="raw", raw=blocks["body"].strip().encode())
    return None
