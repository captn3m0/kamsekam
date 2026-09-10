import copy
import json

import pytest
from werkzeug.wrappers import Response

from kamsekam.archive import derive_output_path, har_request_dict
from kamsekam.cli import main
from kamsekam.parse.curl import parse_curl


def test_derive_output_path_adds_minimal_suffix(tmp_path):
    source = tmp_path / "session.har"
    source.write_text("{}")
    assert derive_output_path(source).name == "session-minimal.har"


def test_derive_output_path_numbers_collisions(tmp_path):
    source = tmp_path / "session.har"
    source.write_text("{}")
    (tmp_path / "session-minimal.har").write_text("{}")
    assert derive_output_path(source).name == "session-minimal-1.har"
    (tmp_path / "session-minimal-1.har").write_text("{}")
    assert derive_output_path(source).name == "session-minimal-2.har"


def test_derive_output_path_without_har_suffix(tmp_path):
    source = tmp_path / "capture.json"
    source.write_text("{}")
    assert derive_output_path(source).name == "capture.json-minimal.har"


def test_har_request_dict_folds_cookies_into_headers():
    request = parse_curl("curl 'https://e.test/x?a=1' -H 'x-k: v' -b 'sid=1'",
                         implicit_headers=False)
    document = har_request_dict(request)
    assert document["method"] == "GET"
    assert {"name": "Cookie", "value": "sid=1"} in document["headers"]
    assert document["cookies"] == [{"name": "sid", "value": "1"}]
    assert document["queryString"] == [{"name": "a", "value": "1"}]


def entry(method, url, headers, status=200):
    return {
        "startedDateTime": "2026-01-01T00:00:00Z",
        "time": 1,
        "request": {
            "method": method,
            "url": url,
            "httpVersion": "HTTP/1.1",
            "cookies": [],
            "headers": [{"name": n, "value": v} for n, v in headers],
            "queryString": [],
            "headersSize": -1,
            "bodySize": 0,
        },
        "response": {"status": status, "statusText": "OK", "httpVersion": "HTTP/1.1",
                     "cookies": [], "headers": [], "content": {"size": 2, "mimeType": "text/plain"},
                     "redirectURL": "", "headersSize": -1, "bodySize": 2},
        "cache": {},
        "timings": {"send": 0, "wait": 1, "receive": 0},
    }


@pytest.fixture
def archive(httpserver, tmp_path):
    httpserver.expect_request("/api").respond_with_handler(
        lambda r: Response("WELCOME", status=200, content_type="text/plain")
        if r.headers.get("X-Key") == "secret" and r.args.get("a") == "1"
        else Response("NO", status=403, content_type="text/plain")
    )
    url = httpserver.url_for("/api") + "?a=1&b=2"
    noise = [("X-Key", "secret"), ("X-Noise", "1"), ("X-More", "2"),
             ("Cookie", "sid=1; theme=dark")]
    document = {
        "log": {
            "version": "1.2",
            "creator": {"name": "test", "version": "1"},
            "entries": [
                entry("GET", url, noise),
                entry("POST", httpserver.url_for("/api"), noise),
            ],
        }
    }
    path = tmp_path / "session.har"
    path.write_text(json.dumps(document))
    return path, copy.deepcopy(document)


def test_archive_minimises_get_and_leaves_post_alone(archive, tmp_path):
    path, original = archive
    out = tmp_path / "minimal.har"
    assert main(["--quiet", "--baseline-runs", "1", "-o", str(out), str(path)]) == 0

    result = json.loads(out.read_text())
    get_headers = {h["name"].lower() for h in result["log"]["entries"][0]["request"]["headers"]}
    assert "x-key" in get_headers
    assert "x-noise" not in get_headers and "x-more" not in get_headers
    assert "cookie" not in get_headers
    assert result["log"]["entries"][1]["request"] == original["log"]["entries"][1]["request"]


def test_archive_keeps_the_rest_of_the_log(archive, tmp_path):
    path, original = archive
    out = tmp_path / "minimal.har"
    main(["--quiet", "--baseline-runs", "1", "-o", str(out), str(path)])
    result = json.loads(out.read_text())
    assert result["log"]["creator"] == original["log"]["creator"]
    assert result["log"]["entries"][0]["response"] == original["log"]["entries"][0]["response"]
    assert result["log"]["entries"][0]["timings"] == original["log"]["entries"][0]["timings"]


def test_archive_derives_the_output_name(archive, tmp_path):
    path, _ = archive
    assert main(["--quiet", "--baseline-runs", "1", str(path)]) == 0
    assert (tmp_path / "session-minimal.har").exists()


def test_archive_derived_name_does_not_overwrite(archive, tmp_path):
    path, _ = archive
    (tmp_path / "session-minimal.har").write_text("sentinel")
    assert main(["--quiet", "--baseline-runs", "1", str(path)]) == 0
    assert (tmp_path / "session-minimal.har").read_text() == "sentinel"
    assert (tmp_path / "session-minimal-1.har").exists()


def test_archive_entry_filter(archive, tmp_path):
    path, original = archive
    out = tmp_path / "one.har"
    assert main(["--quiet", "--baseline-runs", "1", "--format", "har", "--entry", "1",
                 "-o", str(out), str(path)]) == 0
    result = json.loads(out.read_text())
    assert result["log"]["entries"][0]["request"] == original["log"]["entries"][0]["request"]


def test_archive_json_report(archive, tmp_path, capsys):
    path, _ = archive
    out = tmp_path / "minimal.har"
    assert main(["--json", "--baseline-runs", "1", "-o", str(out), str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["output"] == str(out)
    states = {e["index"]: e["state"] for e in report["entries"]}
    assert states == {0: "minimised", 1: "skipped"}
    assert report["entries"][1]["detail"] == "POST request"


def test_har_single_entry_mode_still_emits_curl(archive, capsys):
    path, _ = archive
    assert main(["--quiet", "--baseline-runs", "1", "--format", "curl", str(path)]) == 0
    assert capsys.readouterr().out.strip().startswith("curl ")
