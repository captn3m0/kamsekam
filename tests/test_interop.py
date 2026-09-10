import httpx2
import pytest

from kamsekam.interop import from_httpx2, to_httpx2
from kamsekam.minimizer import minimise
from kamsekam.parse import parse_curl

COMMAND = (
    "curl -X POST 'https://e.test/p?a=1&b=%20c' -H 'x-key: sec ret' "
    "-H 'content-type: application/json' -b 'sid=1; theme=dark' "
    """--data-raw '{"id":7}'"""
)


def test_to_httpx2_is_typed_and_exact():
    request = to_httpx2(parse_curl(COMMAND, implicit_headers=False))
    assert isinstance(request, httpx2.Request)
    assert request.method == "POST"
    assert request.headers["x-key"] == "sec ret"
    assert request.headers["cookie"] == "sid=1; theme=dark"
    assert request.content == b'{"id":7}'


def test_raw_query_encoding_survives():
    request = to_httpx2(parse_curl(COMMAND, implicit_headers=False))
    assert request.url.query == b"a=1&b=%20c"


def test_round_trip_through_httpx2():
    original = parse_curl(COMMAND, implicit_headers=False)
    again = from_httpx2(to_httpx2(original))
    assert again.method == original.method
    assert again.url == original.url
    assert again.cookies == original.cookies
    assert again.body.data == original.body.data
    assert sorted(again.headers) == sorted(original.headers)


def test_from_httpx2_splits_cookies_and_drops_host():
    request = httpx2.Request(
        "GET", "https://e.test/x?a=1", headers=[("Cookie", "u=1; v=2"), ("X-Key", "s")]
    )
    internal = from_httpx2(request)
    assert [p.name for p in internal.cookies] == ["u", "v"]
    assert internal.headers == [("x-key", "s")]
    assert [p.name for p in internal.query] == ["a"]


def test_duplicate_headers_are_preserved():
    request = httpx2.Request("GET", "https://e.test/", headers=[("X-A", "1"), ("X-A", "2")])
    assert from_httpx2(request).headers == [("x-a", "1"), ("x-a", "2")]


def test_minimise_accepts_a_typed_request(httpserver):
    from werkzeug.wrappers import Response

    httpserver.expect_request("/api").respond_with_handler(
        lambda r: Response("OK", status=200, content_type="text/plain")
        if r.headers.get("X-Key") == "secret"
        else Response("NO", status=403, content_type="text/plain")
    )
    request = httpx2.Request(
        "GET",
        httpserver.url_for("/api"),
        headers=[("X-Key", "secret"), ("X-Noise", "1"), ("Cookie", "sid=1; junk=2")],
    )
    result = minimise(request, timeout=10)
    assert {a.id for a in result.kept} == {"header:x-key"}
    assert isinstance(result.as_httpx2(), httpx2.Request)
    assert result.as_httpx2().headers["x-key"] == "secret"
    assert "cookie" not in result.as_httpx2().headers
    assert result.to_curl().startswith("curl ")


def test_minimise_supports_impersonation(httpserver):
    pytest.importorskip("curl_cffi")
    from werkzeug.wrappers import Response

    httpserver.expect_request("/imp").respond_with_handler(
        lambda r: Response("OK", status=200, content_type="text/plain")
        if r.headers.get("X-Key") == "secret"
        else Response("NO", status=403, content_type="text/plain")
    )
    request = httpx2.Request(
        "GET",
        httpserver.url_for("/imp"),
        headers=[("X-Key", "secret"), ("X-Noise", "1"), ("User-Agent", "custom/1")],
    )
    result = minimise(request, impersonate="chrome124", timeout=10)
    assert {a.id for a in result.kept} == {"header:x-key"}
    assert result.impersonate == "chrome124"
    assert "--impersonate chrome124" in result.to_curl()


def test_minimise_without_impersonation_omits_the_flag(httpserver):

    httpserver.expect_request("/plain").respond_with_data("OK", content_type="text/plain")
    request = httpx2.Request("GET", httpserver.url_for("/plain"), headers=[("X-A", "1")])
    result = minimise(request, timeout=10)
    assert result.impersonate is None
    assert "--impersonate" not in result.to_curl()
