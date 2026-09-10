import pytest
from werkzeug.wrappers import Response

from kamsekam.parse.curl import parse_curl
from kamsekam.transport import CurlCffiTransport, Httpx2Transport, TransportError, build_transport


def test_build_transport_defaults_to_httpx2():
    transport = build_transport()
    assert isinstance(transport, Httpx2Transport)
    transport.close()


def test_build_transport_uses_curl_cffi_when_impersonating():
    pytest.importorskip("curl_cffi")
    transport = build_transport(impersonate="chrome124")
    assert isinstance(transport, CurlCffiTransport)
    transport.close()


def test_sends_exactly_the_headers_given(httpserver):
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        return Response("ok", status=200, content_type="text/plain")

    httpserver.expect_request("/x").respond_with_handler(handler)
    request = parse_curl(
        f"curl '{httpserver.url_for('/x')}' -H 'x-key: secret'", implicit_headers=False
    )
    transport = build_transport(timeout=10)
    try:
        response = transport.send(request)
    finally:
        transport.close()

    assert response.status == 200
    assert seen["headers"]["X-Key"] == "secret"
    assert set(seen["headers"]) <= {"Host", "X-Key", "Content-Length", "Connection"}


def test_duplicate_headers_reach_the_server(httpserver):
    # WSGI folds repeated headers into one comma-joined value, so accept either shape
    seen = {}
    httpserver.expect_request("/d").respond_with_handler(
        lambda r: (seen.update(raw=r.headers.getlist("X-Dup")),
                   Response("ok", content_type="text/plain"))[1]
    )
    request = parse_curl(
        f"curl '{httpserver.url_for('/d')}' -H 'x-dup: 1' -H 'x-dup: 2'", implicit_headers=False
    )
    transport = build_transport(timeout=10)
    try:
        transport.send(request)
    finally:
        transport.close()
    assert seen["raw"] in (["1", "2"], ["1,2"])


def test_counts_every_request(httpserver):
    httpserver.expect_request("/c").respond_with_data("ok", content_type="text/plain")
    request = parse_curl(f"curl '{httpserver.url_for('/c')}'", implicit_headers=False)
    transport = build_transport(timeout=10)
    try:
        transport.send(request)
        transport.send(request)
    finally:
        transport.close()
    assert transport.count == 2


def test_unreachable_host_raises_transport_error():
    request = parse_curl("curl http://127.0.0.1:1/nope", implicit_headers=False)
    transport = build_transport(timeout=2, retries=0)
    try:
        with pytest.raises(TransportError):
            transport.send(request)
    finally:
        transport.close()


def test_retries_are_counted():
    request = parse_curl("curl http://127.0.0.1:1/nope", implicit_headers=False)
    transport = build_transport(timeout=2, retries=1)
    try:
        with pytest.raises(TransportError):
            transport.send(request)
    finally:
        transport.close()
    assert transport.count == 2
