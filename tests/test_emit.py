import json

import pytest

from kamsekam.emit import FORMATS, render
from kamsekam.parse import parse_bru, parse_curl, parse_har

COMMAND = (
    "curl -X POST 'https://e.test/p?a=1&b=2' "
    "-H 'accept: application/json, text/plain, */*' "
    "-H 'x-key: sec ret' -H 'content-type: application/json' "
    "-b 'sid=1; theme=dark' "
    """--data-raw '{"id":7,"tags":["a","b"]}' --compressed -L"""
)


@pytest.fixture
def request_obj():
    return parse_curl(COMMAND, implicit_headers=False)


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_produces_something(request_obj, fmt):
    assert render(fmt, request_obj).strip()


def test_curl_round_trips(request_obj):
    again = parse_curl(render("curl", request_obj), implicit_headers=False)
    assert again.method == request_obj.method
    assert again.url == request_obj.url
    assert sorted(again.headers) == sorted(request_obj.headers)
    assert again.cookies == request_obj.cookies
    assert again.body.data == request_obj.body.data
    assert again.follow_redirects


def test_wrapped_curl_round_trips(request_obj):
    wrapped = render("curl", request_obj, wrap=True)
    assert " \\\n" in wrapped
    assert parse_curl(wrapped, implicit_headers=False).url == request_obj.url


def test_curl_quotes_values_with_spaces(request_obj):
    assert "'x-key: sec ret'" in render("curl", request_obj)


def test_curl_suppresses_implicit_content_type():
    request = parse_curl("""curl https://e.test/ --data-raw 'x' -H 'content-type:'""",
                         implicit_headers=False)
    assert "'Content-Type:'" in render("curl", request)


def test_har_round_trips(request_obj):
    again = parse_har(render("har", request_obj))
    assert again.method == request_obj.method
    assert again.url == request_obj.url
    assert [p.name for p in again.cookies] == [p.name for p in request_obj.cookies]
    assert again.body.data == request_obj.body.data


def test_bru_round_trips(request_obj):
    again = parse_bru(render("bru", request_obj))
    assert again.method == request_obj.method
    assert again.base_url == request_obj.base_url
    assert again.header_value("x-key") == "sec ret"
    assert again.body.data == request_obj.body.data


def test_json_report_carries_the_signature(request_obj):
    from kamsekam.oracle import Signature

    signature = Signature(200, "text/html", 4, "d")
    document = json.loads(render("json", request_obj, signature=signature))
    assert document["signature"]["status"] == 200
    assert document["method"] == "POST"


def test_http_wire_format_has_request_line(request_obj):
    text = render("http", request_obj)
    assert text.startswith("POST /p?a=1&b=2 HTTP/1.1\r\nHost: e.test\r\n")
    assert "Cookie: sid=1; theme=dark" in text


def test_impersonate_appears_in_curl(request_obj):
    assert "--impersonate chrome131" in render("curl", request_obj, impersonate="chrome131")


def test_unknown_format_raises(request_obj):
    with pytest.raises(ValueError, match="unknown format"):
        render("cobol", request_obj)
