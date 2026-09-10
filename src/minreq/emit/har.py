"""Render an HttpRequest as a HAR archive, via the harfile writer."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from urllib.parse import urlsplit

from minreq.model import HttpRequest
from minreq.oracle import Signature


def to_har(request: HttpRequest, signature: Signature | None = None) -> str:
    import harfile

    buffer = io.StringIO()
    payload = request.body_bytes()
    post_data = None
    if payload is not None:
        post_data = harfile.PostData(
            mimeType=request.header_value("content-type") or "application/octet-stream",
            text=payload.decode("utf-8", "replace"),
        )
    with harfile.open(buffer, creator=harfile.Creator(name="minreq", version="0.1.0")) as har:
        har.add_entry(
            startedDateTime=datetime.now(timezone.utc).isoformat(),
            time=0,
            request=harfile.Request(
                method=request.method,
                url=request.url,
                httpVersion="HTTP/1.1",
                headers=[harfile.Record(name=n, value=v) for n, v in request.wire_headers()],
                queryString=[harfile.Record(name=p.name, value=p.value) for p in request.query],
                cookies=[harfile.Cookie(name=p.name, value=p.value) for p in request.cookies],
                postData=post_data,
                bodySize=len(payload) if payload else 0,
            ),
            response=harfile.Response(
                status=signature.status if signature else 0,
                statusText="",
                httpVersion="HTTP/1.1",
                content=harfile.Content(
                    size=signature.size if signature else 0,
                    mimeType=signature.content_type if signature else "",
                ),
                bodySize=signature.size if signature else 0,
            ),
            timings=harfile.Timings(send=0, wait=0, receive=0),
        )
    return buffer.getvalue()


def default_name(request: HttpRequest) -> str:
    path = urlsplit(request.base_url).path.strip("/").replace("/", "-")
    return path or "request"
