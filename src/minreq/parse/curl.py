"""Parse a curl command line into an HttpRequest."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from minreq.model import Body, HttpRequest, Pair, classify_body
from minreq.parse.shell import tokenize

IMPLICIT_USER_AGENT = "curl/8.7.1"
COMPRESSED_ENCODINGS = "gzip, deflate, br, zstd"

_NO_ARG = {
    "--compressed",
    "--compressed-ssh",
    "--create-dirs",
    "--fail",
    "--fail-early",
    "--fail-with-body",
    "--get",
    "--globoff",
    "--head",
    "--http0.9",
    "--http1.0",
    "--http1.1",
    "--http2",
    "--http2-prior-knowledge",
    "--http3",
    "--http3-only",
    "--include",
    "--insecure",
    "--ipv4",
    "--ipv6",
    "--junk-session-cookies",
    "--list-only",
    "--location",
    "--location-trusted",
    "--no-alpn",
    "--no-buffer",
    "--no-keepalive",
    "--no-progress-meter",
    "--no-sessionid",
    "--path-as-is",
    "--post301",
    "--post302",
    "--post303",
    "--progress-bar",
    "--raw",
    "--remote-name",
    "--remote-time",
    "--show-error",
    "--silent",
    "--ssl",
    "--ssl-no-revoke",
    "--ssl-reqd",
    "--tcp-nodelay",
    "--tlsv1",
    "--tlsv1.0",
    "--tlsv1.1",
    "--tlsv1.2",
    "--tlsv1.3",
    "--trace-time",
    "--verbose",
    "--xattr",
}

_SHORT_NO_ARG = set("46IJkLOSsvi#")

_TAKES_ARG = {
    "--connect-timeout",
    "--cookie",
    "--cookie-jar",
    "--data",
    "--data-ascii",
    "--data-binary",
    "--data-raw",
    "--data-urlencode",
    "--form",
    "--form-string",
    "--header",
    "--json",
    "--limit-rate",
    "--max-filesize",
    "--max-redirs",
    "--max-time",
    "--oauth2-bearer",
    "--output",
    "--proxy",
    "--proxy-user",
    "--range",
    "--referer",
    "--request",
    "--resolve",
    "--retry",
    "--upload-file",
    "--url",
    "--user",
    "--user-agent",
    "--write-out",
}

_SHORT_TO_LONG = {
    "A": "--user-agent",
    "b": "--cookie",
    "c": "--cookie-jar",
    "d": "--data",
    "e": "--referer",
    "F": "--form",
    "G": "--get",
    "H": "--header",
    "I": "--head",
    "k": "--insecure",
    "L": "--location",
    "m": "--max-time",
    "o": "--output",
    "r": "--range",
    "T": "--upload-file",
    "u": "--user",
    "x": "--proxy",
    "X": "--request",
}


class CurlParseError(ValueError):
    pass


def _read_at_file(spec: str) -> str:
    if spec == "-":
        return sys.stdin.read()
    return Path(spec).expanduser().read_text()


class _Options:
    def __init__(self) -> None:
        self.method: str | None = None
        self.urls: list[str] = []
        self.headers: list[tuple[str, str]] = []
        self.suppressed: set[str] = set()
        self.cookies: list[str] = []
        self.data: list[tuple[str, str]] = []
        self.form: list[tuple[str, str]] = []
        self.follow = False
        self.insecure = False
        self.compressed = False
        self.get = False
        self.head = False
        self.user: str | None = None
        self.bearer: str | None = None
        self.proxy: str | None = None
        self.http_version: str | None = None
        self.upload: str | None = None
        self.json_body: list[str] = []


def _consume(opts: _Options, flag: str, value: str | None) -> None:
    if flag == "--header":
        assert value is not None
        _add_header(opts, value)
    elif flag == "--user-agent":
        opts.headers.append(("User-Agent", value or ""))
    elif flag == "--referer":
        opts.headers.append(("Referer", value or ""))
    elif flag == "--cookie":
        assert value is not None
        if "=" in value:
            opts.cookies.append(value)
    elif flag in ("--data", "--data-raw", "--data-ascii", "--data-binary", "--data-urlencode"):
        assert value is not None
        opts.data.append((flag, value))
    elif flag == "--json":
        assert value is not None
        opts.json_body.append(value)
    elif flag in ("--form", "--form-string"):
        assert value is not None
        opts.form.append((flag, value))
    elif flag == "--request":
        opts.method = (value or "GET").upper()
    elif flag == "--url":
        assert value is not None
        opts.urls.append(value)
    elif flag == "--user":
        opts.user = value
    elif flag == "--oauth2-bearer":
        opts.bearer = value
    elif flag == "--proxy":
        opts.proxy = value
    elif flag == "--upload-file":
        opts.upload = value
    elif flag in ("--location", "--location-trusted"):
        opts.follow = True
    elif flag == "--insecure":
        opts.insecure = True
    elif flag == "--compressed":
        opts.compressed = True
    elif flag == "--get":
        opts.get = True
    elif flag == "--head":
        opts.head = True
    elif flag.startswith("--http"):
        opts.http_version = flag[2:]


def _add_header(opts: _Options, spec: str) -> None:
    if spec.startswith("@"):
        for line in _read_at_file(spec[1:]).splitlines():
            if line.strip():
                _add_header(opts, line.strip())
        return
    if ":" not in spec:
        if spec.endswith(";"):
            opts.headers.append((spec[:-1].strip(), ""))
        return
    name, _, value = spec.partition(":")
    name = name.strip()
    value = value.strip()
    if not value:
        opts.suppressed.add(name.lower())
        return
    opts.headers.append((name, value))


def _parse_tokens(tokens: list[str]) -> _Options:
    opts = _Options()
    i = 0
    if tokens and Path(tokens[0]).name.startswith("curl"):
        i = 1
    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token == "--":
            opts.urls.extend(tokens[i:])
            break
        if token.startswith("--"):
            flag, sep, inline = token.partition("=")
            if flag in _NO_ARG:
                _consume(opts, flag, None)
            elif flag in _TAKES_ARG:
                if sep:
                    _consume(opts, flag, inline)
                elif i < len(tokens):
                    _consume(opts, flag, tokens[i])
                    i += 1
            elif flag.startswith("--no-") or sep:
                continue
            elif i < len(tokens) and not tokens[i].startswith("-"):
                i += 1
        elif token.startswith("-") and len(token) > 1:
            j = 1
            while j < len(token):
                short = token[j]
                j += 1
                if short in _SHORT_NO_ARG:
                    _consume(opts, _SHORT_TO_LONG.get(short, "--" + short), None)
                    continue
                long = _SHORT_TO_LONG.get(short)
                if long is None:
                    continue
                if long in _TAKES_ARG:
                    if j < len(token):
                        _consume(opts, long, token[j:])
                        j = len(token)
                    elif i < len(tokens):
                        _consume(opts, long, tokens[i])
                        i += 1
                else:
                    _consume(opts, long, None)
        else:
            opts.urls.append(token)
    return opts


def _urlencode_data(value: str) -> str:
    if value.startswith("@"):
        return quote(_read_at_file(value[1:]), safe="")
    name, sep, rest = value.partition("=")
    if sep:
        return f"{name}={quote(rest, safe='')}" if name else quote(rest, safe="")
    name, sep, filename = value.partition("@")
    if sep:
        return f"{name}={quote(_read_at_file(filename), safe='')}"
    return quote(value, safe="")


def _collect_data(opts: _Options) -> str:
    chunks: list[str] = []
    for flag, value in opts.data:
        if flag == "--data-urlencode":
            chunks.append(_urlencode_data(value))
        elif flag == "--data-raw" or not value.startswith("@"):
            chunks.append(value)
        else:
            text = _read_at_file(value[1:])
            if flag != "--data-binary":
                text = text.replace("\r", "").replace("\n", "")
            chunks.append(text)
    return "&".join(chunks)


def _build_form_body(opts: _Options) -> Body:
    fields: list[Pair] = []
    for flag, spec in opts.form:
        name, _, value = spec.partition("=")
        if flag == "--form" and value.startswith("@"):
            value = Path(value[1:].split(";")[0]).expanduser().read_text()
        elif flag == "--form" and value.startswith("<"):
            value = Path(value[1:]).expanduser().read_text()
        fields.append(Pair(spec, name, value))
    return Body(kind="multipart", fields=fields)


def parse_curl(command: str | list[str], implicit_headers: bool = True) -> HttpRequest:
    """Turn a curl command (string or already-split argv) into an HttpRequest."""
    tokens = tokenize(command) if isinstance(command, str) else list(command)
    if not tokens:
        raise CurlParseError("empty curl command")
    opts = _parse_tokens(tokens)
    if not opts.urls:
        raise CurlParseError("no URL found in curl command")

    raw_url = opts.urls[0]
    if "://" not in raw_url:
        raw_url = "https://" + raw_url
    parts = urlsplit(raw_url)
    base_url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", "", ""))
    query = [Pair.from_query(seg) for seg in parts.query.split("&") if seg]

    headers = list(opts.headers)
    cookies: list[Pair] = []
    for spec in opts.cookies:
        cookies.extend(Pair.from_cookie(s) for s in spec.split(";") if s.strip())
    for name, value in list(headers):
        if name.lower() == "cookie":
            headers.remove((name, value))
            cookies.extend(Pair.from_cookie(s) for s in value.split(";") if s.strip())

    if opts.user:
        token = base64.b64encode(opts.user.encode()).decode()
        headers.append(("Authorization", f"Basic {token}"))
    if opts.bearer:
        headers.append(("Authorization", f"Bearer {opts.bearer}"))

    payload = _collect_data(opts)
    body: Body | None = None
    if opts.json_body:
        payload = "".join(opts.json_body)
        body = classify_body(payload, "application/json")
        _ensure_header(headers, "Content-Type", "application/json")
        _ensure_header(headers, "Accept", "application/json")
    elif opts.form:
        body = _build_form_body(opts)
    elif payload and not opts.get:
        body = classify_body(payload, _find_header(headers, "content-type"))
        if body.kind == "form":
            _ensure_header(headers, "Content-Type", "application/x-www-form-urlencoded")
    elif payload and opts.get:
        query.extend(Pair.from_query(seg) for seg in payload.split("&") if seg)

    if opts.compressed:
        _ensure_header(headers, "Accept-Encoding", COMPRESSED_ENCODINGS)
    if opts.upload:
        body = Body(kind="raw", raw=Path(opts.upload).expanduser().read_bytes())

    method = opts.method
    if method is None:
        if opts.head:
            method = "HEAD"
        elif opts.upload:
            method = "PUT"
        elif body is not None:
            method = "POST"
        else:
            method = "GET"

    if implicit_headers:
        _ensure_header(headers, "User-Agent", IMPLICIT_USER_AGENT)
        _ensure_header(headers, "Accept", "*/*")

    dropped = opts.suppressed | {"content-length"}
    headers = [(n, v) for n, v in headers if n.lower() not in dropped]

    return HttpRequest(
        method=method,
        base_url=base_url,
        query=query,
        headers=headers,
        cookies=cookies,
        body=body,
        follow_redirects=opts.follow,
        insecure=opts.insecure,
        http_version=opts.http_version,
        proxy=opts.proxy,
    )


def _find_header(headers: list[tuple[str, str]], name: str) -> str | None:
    for key, value in headers:
        if key.lower() == name.lower():
            return value
    return None


def _ensure_header(headers: list[tuple[str, str]], name: str, value: str) -> None:
    if _find_header(headers, name) is None:
        headers.append((name, value))
