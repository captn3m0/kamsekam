import json

import pytest
from werkzeug.wrappers import Response

from kamsekam.atoms import decompose
from kamsekam.cli import main
from kamsekam.minimizer import Minimizer
from kamsekam.oracle import DEFAULT_MATCH, Oracle, Signature
from kamsekam.parse import parse_curl
from kamsekam.parse.shell import tokenize
from kamsekam.transport import build_transport


def guarded(request):
    ok = (
        request.headers.get("X-Key") == "secret"
        and request.cookies.get("sid") == "9f2a"
        and request.args.get("a") == "1"
    )
    if not ok:
        return Response("DENIED", status=403, content_type="text/plain")
    return Response("WELCOME HOME", status=200, content_type="text/plain")


@pytest.fixture
def endpoint(httpserver):
    httpserver.expect_request("/api").respond_with_handler(guarded)
    return httpserver.url_for("/api")


def noisy_command(endpoint):
    return (
        f"curl '{endpoint}?a=1&b=2&trace=off' "
        "-H 'accept: application/json, text/plain, */*' "
        "-H 'accept-language: en-US,en;q=0.9' "
        "-H 'x-key: secret' -H 'x-noise: 1' -H 'x-more: 2' "
        "-b 'sid=9f2a; theme=dark; _ga=GA1.2.99; consent=yes'"
    )


def minimise_command(command, **kwargs):
    decomposition = decompose(parse_curl(command, implicit_headers=False))
    transport = build_transport(timeout=10)
    try:
        baseline = Signature.of(transport.send(decomposition.request))
        oracle = Oracle(transport, baseline, DEFAULT_MATCH)
        return decomposition, Minimizer(decomposition, oracle, **kwargs).run()
    finally:
        transport.close()


def test_finds_exactly_the_required_atoms(endpoint):
    _, result = minimise_command(noisy_command(endpoint))
    assert {a.id for a in result.kept} == {"header:x-key", "cookie:sid", "query:a"}
    assert result.baseline.status == 200
    assert result.bytes_saved > 100


def test_minimised_request_still_works(endpoint):
    _, result = minimise_command(noisy_command(endpoint))
    transport = build_transport(timeout=10)
    try:
        assert Signature.of(transport.send(result.request)) == result.baseline
    finally:
        transport.close()


def test_result_is_one_minimal_against_the_server(endpoint):
    decomposition, result = minimise_command(noisy_command(endpoint))
    transport = build_transport(timeout=10)
    kept = {a.id for a in result.kept}
    try:
        for atom_id in kept:
            trimmed = decomposition.rebuild(kept - {atom_id})
            assert Signature.of(transport.send(trimmed)) != result.baseline
    finally:
        transport.close()


def test_keep_pattern_pins_an_atom(endpoint):
    _, result = minimise_command(noisy_command(endpoint), pinned={"cookie:theme"})
    assert any(p.name == "theme" for p in result.request.cookies)


def test_cli_emits_a_working_curl_command(endpoint, capsys):
    argv = ["--quiet", "--baseline-runs", "1", "--", *tokenize(noisy_command(endpoint))]
    assert main(argv) == 0
    printed = capsys.readouterr().out.strip()
    assert printed.startswith("curl ")
    again = parse_curl(printed, implicit_headers=False)
    assert again.header_value("x-key") == "secret"
    assert [p.name for p in again.cookies] == ["sid"]
    assert [p.name for p in again.query] == ["a"]


def test_cli_json_report(endpoint, capsys):
    argv = ["--json", "--baseline-runs", "1", "--", "curl", f"{endpoint}?a=1&b=2",
            "-H", "x-key: secret", "-H", "x-noise: 1", "-b", "sid=9f2a; theme=dark"]
    assert main(argv) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["baseline"]["status"] == 200
    assert document["stats"]["requests"] > 0
    assert sorted(a["id"] for a in document["atoms"]["kept"]) == [
        "cookie:sid", "header:x-key", "query:a"
    ]
    assert document["output"]["curl"].startswith("curl ")


def test_cli_multiple_formats(endpoint, capsys):
    argv = ["--quiet", "--baseline-runs", "1", "--format", "curl,http,bru", "--",
            "curl", f"{endpoint}?a=1", "-H", "x-key: secret", "-b", "sid=9f2a"]
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "curl " in out and "HTTP/1.1" in out and "meta {" in out


def test_cli_dry_run_sends_nothing(capsys):
    argv = ["--dry-run", "--", "curl", "https://192.0.2.1/x?a=1", "-H", "x-key: secret"]
    assert main(argv) == 0
    assert "query:a" in capsys.readouterr().out


def test_cli_reports_unreachable_origin(capsys):
    argv = ["--retries", "0", "--timeout", "2", "--", "curl", "http://127.0.0.1:1/nope"]
    assert main(argv) == 1
    assert "could not be sent" in capsys.readouterr().err


def test_cli_rejects_bad_format(capsys):
    assert main(["--format", "cobol", "--", "curl", "https://e.test/"]) == 2
    assert "unknown format" in capsys.readouterr().err


def test_unstable_body_relaxes_to_size(httpserver, capsys):
    counter = {"n": 0}

    def jitter(request):
        counter["n"] += 1
        keep = request.headers.get("X-Key") == "secret"
        body = f"token={counter['n']:04d}" if keep else "no"
        return Response(body, status=200 if keep else 403, content_type="text/plain")

    httpserver.expect_request("/j").respond_with_handler(jitter)
    url = httpserver.url_for("/j")
    argv = ["--", "curl", url, "-H", "x-key: secret", "-H", "x-noise: 1", "-b", "sid=1"]
    assert main(argv) == 0
    err = capsys.readouterr().err
    assert "matching on size instead of hash" in err
    assert "x-key" in err


def test_quiet_suppresses_the_whole_report(endpoint, capsys):
    argv = ["--quiet", "--baseline-runs", "1", "--", "curl", f"{endpoint}?a=1",
            "-H", "x-key: secret", "-b", "sid=9f2a"]
    assert main(argv) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.strip().startswith("curl ")
