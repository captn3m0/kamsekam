"""Canonical HTTP request model shared by every parser, emitter and transport."""

from __future__ import annotations

import json as jsonlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit

LIST_HEADERS = frozenset(
    "accept accept-charset accept-encoding accept-language accept-patch accept-post "
    "access-control-request-headers allow cache-control clear-site-data connection "
    "content-encoding content-language expect forwarded if-match if-none-match permissions-policy "
    "pragma prefer sec-ch-ua sec-ch-ua-form-factors sec-ch-ua-full-version-list te trailer "
    "transfer-encoding upgrade vary via want-digest warning x-forwarded-for"
    .split()
)

MULTIPART_BOUNDARY = "----kamsekamFormBoundary7MA4YWxkTrZu0gW"


def split_list_value(value: str) -> list[str]:
    """Split a comma-separated header value, ignoring commas inside quotes."""
    parts: list[str] = []
    buf: list[str] = []
    quoted = False
    escaped = False
    for ch in value:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\" and quoted:
            buf.append(ch)
            escaped = True
        elif ch == '"':
            quoted = not quoted
            buf.append(ch)
        elif ch == "," and not quoted:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


def is_list_header(name: str) -> bool:
    return name.lower() in LIST_HEADERS


@dataclass(frozen=True)
class Pair:
    """A name/value pair that remembers exactly how it was written on the wire."""

    raw: str
    name: str
    value: str

    @classmethod
    def from_query(cls, raw: str) -> Pair:
        name, sep, value = raw.partition("=")
        return cls(raw, unquote_plus(name), unquote_plus(value) if sep else "")

    @classmethod
    def from_cookie(cls, raw: str) -> Pair:
        name, sep, value = raw.partition("=")
        return cls(raw.strip(), name.strip(), value.strip() if sep else "")

    @classmethod
    def encoded(cls, name: str, value: str) -> Pair:
        raw = f"{quote(name, safe='')}={quote(value, safe='')}"
        return cls(raw, name, value)


@dataclass
class Body:
    """A request body in whichever shape lets us take it apart usefully."""

    kind: str = "raw"
    raw: bytes = b""
    fields: list[Pair] = field(default_factory=list)
    data: Any = None

    def render(self) -> bytes:
        if self.kind == "form":
            return "&".join(p.raw for p in self.fields).encode()
        if self.kind == "json":
            return self._render_json()
        if self.kind == "multipart":
            return self._render_multipart()
        return self.raw

    def _render_json(self) -> bytes:
        """Emit the original bytes while the data still matches them, so nothing drifts."""
        if self.raw:
            try:
                if jsonlib.loads(self.raw) == self.data:
                    return self.raw
            except ValueError:
                pass
        return jsonlib.dumps(self.data, separators=(",", ":")).encode()

    def _render_multipart(self) -> bytes:
        out = bytearray()
        for p in self.fields:
            out += f"--{MULTIPART_BOUNDARY}\r\n".encode()
            out += f'Content-Disposition: form-data; name="{p.name}"\r\n\r\n'.encode()
            out += p.value.encode() + b"\r\n"
        out += f"--{MULTIPART_BOUNDARY}--\r\n".encode()
        return bytes(out)

    def is_empty(self) -> bool:
        if self.kind in ("form", "multipart"):
            return not self.fields
        if self.kind == "json":
            return self.data is None or self.data == {} or self.data == []
        return not self.raw

    def text(self) -> str:
        return self.render().decode("utf-8", "replace")


def looks_like_json(payload: str) -> bool:
    stripped = payload.strip()
    return stripped.startswith(("{", "[")) and stripped.endswith(("}", "]"))


def looks_like_form(payload: str) -> bool:
    return bool(payload) and "=" in payload and "\n" not in payload


def classify_body(payload: str | bytes, content_type: str | None = None) -> Body:
    """Pick the body shape that lets us take the payload apart most usefully."""
    if isinstance(payload, bytes):
        try:
            text = payload.decode()
        except UnicodeDecodeError:
            return Body(kind="raw", raw=payload)
    else:
        text = payload
    raw = text.encode()
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind == "application/json" or (not content_type and looks_like_json(text)):
        try:
            return Body(kind="json", data=jsonlib.loads(text), raw=raw)
        except ValueError:
            pass
    if kind in ("", "application/x-www-form-urlencoded") and looks_like_form(text):
        return Body(kind="form", fields=[Pair.from_query(s) for s in text.split("&") if s])
    return Body(kind="raw", raw=raw)


@dataclass
class HttpRequest:
    """A request, decomposed just enough that pieces of it can be removed."""

    method: str = "GET"
    base_url: str = ""
    query: list[Pair] = field(default_factory=list)
    headers: list[tuple[str, str]] = field(default_factory=list)
    cookies: list[Pair] = field(default_factory=list)
    body: Body | None = None
    follow_redirects: bool = False
    insecure: bool = False
    http_version: str | None = None
    proxy: str | None = None

    @property
    def url(self) -> str:
        parts = urlsplit(self.base_url)
        query = "&".join(p.raw for p in self.query)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))

    def header_value(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return None

    def wire_headers(self) -> list[tuple[str, str]]:
        """Headers as actually sent, with the Cookie header folded back in."""
        out = list(self.headers)
        if self.cookies:
            out.append(("Cookie", "; ".join(p.raw for p in self.cookies)))
        multipart = self.body is not None and self.body.kind == "multipart"
        if multipart and self.header_value("content-type") is None:
            out.append(("Content-Type", f"multipart/form-data; boundary={MULTIPART_BOUNDARY}"))
        return out

    def body_bytes(self) -> bytes | None:
        if self.body is None or self.body.is_empty():
            return None
        return self.body.render()

    def size(self) -> int:
        n = len(f"{self.method} {self.url} HTTP/1.1\r\n".encode())
        for name, value in self.wire_headers():
            n += len(f"{name}: {value}\r\n".encode())
        payload = self.body_bytes()
        return n + 2 + (len(payload) if payload else 0)

    def to_curl(self, impersonate: str | None = None) -> str:
        from kamsekam.emit.curl import to_curl

        return to_curl(self, impersonate=impersonate)
