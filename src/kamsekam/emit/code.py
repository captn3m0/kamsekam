"""Render an HttpRequest as a snippet in whichever client the reader uses."""

from __future__ import annotations

import json as jsonlib

from kamsekam.emit.curl import quote
from kamsekam.model import HttpRequest


def _headers_dict(request: HttpRequest) -> dict[str, str]:
    out = {name: value for name, value in request.headers}
    if request.cookies:
        out["Cookie"] = "; ".join(p.raw for p in request.cookies)
    return out


def _py(request: HttpRequest, module: str) -> str:
    lines = [f"import {module}", ""]
    headers = _headers_dict(request)
    if headers:
        lines.append("headers = " + jsonlib.dumps(headers, indent=4).replace('"', '"'))
    payload = request.body_bytes()
    body = request.body
    if body is not None and body.kind == "json" and payload:
        lines.append("payload = " + jsonlib.dumps(body.data, indent=4))
    elif payload:
        lines.append("payload = " + repr(payload))

    args = [repr(request.url)]
    if headers:
        args.append("headers=headers")
    if body is not None and body.kind == "json" and payload:
        args.append("json=payload")
    elif payload:
        args.append("data=payload")
    if request.follow_redirects and module == "requests":
        args.append("allow_redirects=True")
    if module == "httpx":
        args.append(f"follow_redirects={request.follow_redirects}")
    if request.insecure:
        args.append("verify=False")

    call = f"{module}.{request.method.lower()}(" if request.method.lower() in (
        "get", "post", "put", "delete", "patch", "head", "options"
    ) else f'{module}.request("{request.method}", '
    lines += ["", f"response = {call}" + ", ".join(args) + ")", "print(response.status_code)"]
    return "\n".join(lines)


def to_requests(request: HttpRequest) -> str:
    return _py(request, "requests")


def to_httpx(request: HttpRequest) -> str:
    return _py(request, "httpx")


def to_fetch(request: HttpRequest) -> str:
    options: dict[str, object] = {"method": request.method}
    headers = _headers_dict(request)
    if headers:
        options["headers"] = headers
    payload = request.body_bytes()
    if payload is not None:
        options["body"] = payload.decode("utf-8", "replace")
    if not request.follow_redirects:
        options["redirect"] = "manual"
    body = jsonlib.dumps(options, indent=2)
    return f"const response = await fetch({jsonlib.dumps(request.url)}, {body});"


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
    if body is not None and body.kind == "json" and body.data is not None:
        for key, value in (body.data.items() if isinstance(body.data, dict) else []):
            operator = "=" if isinstance(value, str) else ":="
            rendered = value if isinstance(value, str) else jsonlib.dumps(value)
            parts.append(quote(f"{key}{operator}{rendered}"))
    return " ".join(parts)


def to_wget(request: HttpRequest) -> str:
    parts = ["wget", "-qO-"]
    if request.method != "GET":
        parts.append(f"--method={request.method}")
    for name, value in request.headers:
        parts += ["--header", quote(f"{name}: {value}")]
    if request.cookies:
        parts += ["--header", quote("Cookie: " + "; ".join(p.raw for p in request.cookies))]
    payload = request.body_bytes()
    if payload is not None:
        parts += ["--body-data", quote(payload.decode("utf-8", "replace"))]
    if not request.follow_redirects:
        parts.append("--max-redirect=0")
    if request.insecure:
        parts.append("--no-check-certificate")
    parts.append(quote(request.url))
    return " ".join(parts)


def to_powershell(request: HttpRequest) -> str:
    lines = ["$headers = @{}"]
    for name, value in _headers_dict(request).items():
        lines.append(f'$headers["{name}"] = {jsonlib.dumps(value)}')
    args = [f"-Uri {jsonlib.dumps(request.url)}", f"-Method {request.method}", "-Headers $headers"]
    payload = request.body_bytes()
    if payload is not None:
        lines.append(f"$body = {jsonlib.dumps(payload.decode('utf-8', 'replace'))}")
        args.append("-Body $body")
    if not request.follow_redirects:
        args.append("-MaximumRedirection 0")
    lines.append("Invoke-WebRequest " + " ".join(args))
    return "\n".join(lines)


def to_http(request: HttpRequest) -> str:
    from urllib.parse import urlsplit

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
