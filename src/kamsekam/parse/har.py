"""Parse a HAR archive entry into an HttpRequest."""

from __future__ import annotations

import json as jsonlib
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from kamsekam.model import Body, HttpRequest, Pair

SKIPPED_HEADERS = {"cookie", "content-length", "host", "connection"}


class HarParseError(ValueError):
    pass


def list_entries(document: str | dict[str, Any]) -> list[tuple[str, str, int]]:
    """Return (method, url, status) for every entry, for `--entry` selection."""
    har = jsonlib.loads(document) if isinstance(document, str) else document
    entries = har.get("log", {}).get("entries", [])
    return [
        (e.get("request", {}).get("method", "GET"), e.get("request", {}).get("url", ""),
         e.get("response", {}).get("status", 0))
        for e in entries
    ]


def parse_har(document: str | dict[str, Any], entry: int | str = 0) -> HttpRequest:
    har = jsonlib.loads(document) if isinstance(document, str) else document
    entries = har.get("log", {}).get("entries", [])
    if not entries:
        raise HarParseError("HAR file contains no entries")
    if isinstance(entry, str):
        matches = [i for i, e in enumerate(entries) if entry in e.get("request", {}).get("url", "")]
        if not matches:
            raise HarParseError(f"no HAR entry matching {entry!r}")
        index = matches[0]
    else:
        index = entry
    if index >= len(entries):
        raise HarParseError(f"HAR has {len(entries)} entries, asked for index {index}")
    return _from_request(entries[index].get("request", {}))


def _from_request(req: dict[str, Any]) -> HttpRequest:
    raw_url = req.get("url", "")
    if not raw_url:
        raise HarParseError("HAR entry has no URL")
    parts = urlsplit(raw_url)
    base_url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
    query = [Pair.from_query(seg) for seg in parts.query.split("&") if seg]

    headers: list[tuple[str, str]] = []
    cookies: list[Pair] = []
    for item in req.get("headers", []):
        name = item.get("name", "")
        value = item.get("value", "")
        if not name or name.startswith(":"):
            continue
        if name.lower() == "cookie":
            cookies.extend(Pair.from_cookie(s) for s in value.split(";") if s.strip())
            continue
        if name.lower() in SKIPPED_HEADERS:
            continue
        headers.append((name, value))

    if not cookies:
        for item in req.get("cookies", []):
            name = item.get("name")
            if name:
                cookies.append(Pair.from_cookie(f"{name}={item.get('value', '')}"))

    return HttpRequest(
        method=req.get("method", "GET").upper(),
        base_url=base_url,
        query=query,
        headers=headers,
        cookies=cookies,
        body=_body_from_post_data(req.get("postData")),
    )


def _body_from_post_data(post: dict[str, Any] | None) -> Body | None:
    if not post:
        return None
    mime = (post.get("mimeType") or "").split(";")[0].strip().lower()
    params = post.get("params") or []
    text = post.get("text") or ""
    if mime == "application/x-www-form-urlencoded":
        if params:
            fields = [Pair.encoded(p.get("name", ""), p.get("value", "")) for p in params]
        else:
            fields = [Pair.from_query(s) for s in text.split("&") if s]
        return Body(kind="form", fields=fields)
    if mime == "multipart/form-data" and params:
        return Body(
            kind="multipart",
            fields=[
                Pair(f"{p.get('name', '')}={p.get('value', '')}", p.get("name", ""),
                     p.get("value", ""))
                for p in params
            ],
        )
    if not text:
        return None
    if mime == "application/json" or text.strip()[:1] in "{[":
        try:
            return Body(kind="json", data=jsonlib.loads(text), raw=text.encode())
        except ValueError:
            pass
    return Body(kind="raw", raw=text.encode())
