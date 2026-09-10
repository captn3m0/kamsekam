import base64

import pytest

from kamsekam.parse.curl import CurlParseError, parse_curl


def parse(command, **kwargs):
    return parse_curl(command, implicit_headers=False, **kwargs)


def test_minimal_get():
    request = parse("curl https://example.com/x")
    assert (request.method, request.base_url) == ("GET", "https://example.com/x")


def test_bare_host_gets_https():
    assert parse("curl example.com").base_url == "https://example.com/"


def test_headers_and_cookies_split():
    request = parse("curl -H 'a: 1' -b 'x=1; y=2' https://e.test/")
    assert request.headers == [("a", "1")]
    assert [(p.name, p.value) for p in request.cookies] == [("x", "1"), ("y", "2")]


def test_cookie_header_becomes_cookies():
    request = parse("curl -H 'Cookie: x=1; y=2' https://e.test/")
    assert request.headers == []
    assert [p.name for p in request.cookies] == ["x", "y"]


def test_data_raw_implies_post_and_json_body():
    request = parse("""curl https://e.test/ --data-raw '{"a":1}'""")
    assert request.method == "POST"
    assert request.body.kind == "json" and request.body.data == {"a": 1}


def test_form_data_is_decomposable():
    request = parse("curl https://e.test/ -d 'a=1&b=2'")
    assert request.body.kind == "form"
    assert [p.name for p in request.body.fields] == ["a", "b"]
    assert request.header_value("Content-Type") == "application/x-www-form-urlencoded"


def test_get_flag_moves_data_to_query():
    request = parse("curl -G https://e.test/ -d 'a=1&b=2'")
    assert request.method == "GET"
    assert [p.name for p in request.query] == ["a", "b"]


def test_basic_auth_becomes_a_header():
    request = parse("curl -u alice:s3cret https://e.test/")
    token = base64.b64encode(b"alice:s3cret").decode()
    assert request.header_value("authorization") == f"Basic {token}"


def test_bundled_short_flags():
    request = parse("curl -sSLk https://e.test/")
    assert request.follow_redirects and request.insecure


def test_compressed_sets_accept_encoding():
    assert "gzip" in parse("curl --compressed https://e.test/").header_value("accept-encoding")


def test_empty_header_value_suppresses():
    assert parse("curl -H 'Accept:' -H 'a: 1' https://e.test/").header_value("accept") is None


def test_header_with_semicolon_sends_empty():
    assert parse("curl -H 'X-Empty;' https://e.test/").header_value("x-empty") == ""


def test_equals_form_long_options():
    request = parse("curl --header='a: 1' --request=PUT https://e.test/")
    assert request.method == "PUT" and request.headers == [("a", "1")]


def test_head_flag():
    assert parse("curl -I https://e.test/").method == "HEAD"


def test_argv_list_input():
    request = parse_curl(["curl", "-H", "a: 1", "https://e.test/"], implicit_headers=False)
    assert request.headers == [("a", "1")]


def test_implicit_headers_match_curl_defaults():
    request = parse_curl("curl https://e.test/")
    assert request.header_value("user-agent").startswith("curl/")
    assert request.header_value("accept") == "*/*"


def test_no_url_raises():
    with pytest.raises(CurlParseError):
        parse("curl -H 'a: 1'")


def test_content_length_is_dropped():
    request = parse("curl -H 'Content-Length: 99' https://e.test/ -d x=1")
    assert request.header_value("content-length") is None


def test_range_becomes_a_range_header():
    assert parse("curl -r 0-1023 https://e.test/").header_value("range") == "bytes=0-1023"
    assert parse("curl --range 500- https://e.test/").header_value("range") == "bytes=500-"


def test_range_with_an_explicit_unit_is_passed_through():
    assert parse("curl -r items=1-2 https://e.test/").header_value("range") == "items=1-2"


def test_unknown_flags_do_not_swallow_the_url():
    for command in (
        "curl --future-thing https://e.test/x",
        "curl --shiny-new https://e.test/x -H 'a: b'",
        "curl --a --b https://e.test/x",
        "curl --future-thing e.test/x",
    ):
        assert "e.test/x" in parse(command).base_url, command
