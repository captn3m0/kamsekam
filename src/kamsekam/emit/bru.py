"""Render an HttpRequest as a Bruno .bru file."""

from __future__ import annotations

import json as jsonlib
from urllib.parse import urlsplit

from kamsekam.model import HttpRequest


def to_bru(request: HttpRequest, name: str | None = None, seq: int = 1) -> str:
    name = name or (urlsplit(request.base_url).path.strip("/").replace("/", "-") or "request")
    body = request.body
    body_kind = "none"
    if body is not None and not body.is_empty():
        body_kind = {
            "json": "json",
            "form": "formUrlEncoded",
            "multipart": "multipartForm",
        }.get(body.kind, "text")

    blocks = [
        f"meta {{\n  name: {name}\n  type: http\n  seq: {seq}\n}}",
        f"{request.method.lower()} {{\n  url: {request.url}\n"
        f"  body: {body_kind}\n  auth: none\n}}",
    ]

    if request.query:
        rows = "\n".join(f"  {p.name}: {p.value}" for p in request.query)
        blocks.append(f"params:query {{\n{rows}\n}}")

    header_rows = [f"  {n}: {v}" for n, v in request.headers]
    if request.cookies:
        header_rows.append("  Cookie: " + "; ".join(p.raw for p in request.cookies))
    if header_rows:
        blocks.append("headers {\n" + "\n".join(header_rows) + "\n}")

    if body is not None and not body.is_empty():
        blocks.append(_body_block(request, body_kind))

    return "\n\n".join(blocks) + "\n"


def _body_block(request: HttpRequest, body_kind: str) -> str:
    body = request.body
    assert body is not None
    if body_kind == "json":
        text = jsonlib.dumps(body.data, indent=2)
        indented = "\n".join(f"  {line}" for line in text.splitlines())
        return f"body:json {{\n{indented}\n}}"
    if body_kind == "formUrlEncoded":
        rows = "\n".join(f"  {p.name}: {p.value}" for p in body.fields)
        return f"body:form-urlencoded {{\n{rows}\n}}"
    if body_kind == "multipartForm":
        rows = "\n".join(f"  {p.name}: {p.value}" for p in body.fields)
        return f"body:multipart-form {{\n{rows}\n}}"
    indented = "\n".join(f"  {line}" for line in body.text().splitlines())
    return f"body:text {{\n{indented}\n}}"
