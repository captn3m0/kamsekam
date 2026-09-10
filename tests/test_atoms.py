from minreq.atoms import decompose
from minreq.parse.curl import parse_curl

COMMAND = (
    "curl 'https://e.test/p?a=1&b=2' -H 'accept: application/json, text/plain, */*' "
    "-H 'x-key: secret' -b 'sid=1; theme=dark' "
    """--data-raw '{"id":7,"opts":{"deep":true}}' -H 'content-type: application/json'"""
)


def build():
    return decompose(parse_curl(COMMAND, implicit_headers=False))


def test_multi_value_header_splits_into_elements():
    ids = build().ids
    assert "header:accept[0]" in ids and "header:accept[2]" in ids
    assert "header:accept" not in ids


def test_every_kind_is_represented():
    kinds = {a.kind for a in build().atoms}
    assert kinds == {"header", "header-element", "query", "cookie", "json"}


def test_rebuild_with_everything_matches_the_original():
    decomposition = build()
    rebuilt = decomposition.rebuild(set(decomposition.ids))
    assert rebuilt.url == decomposition.request.url
    assert rebuilt.cookies == decomposition.request.cookies
    assert rebuilt.body.data == decomposition.request.body.data


def test_dropping_all_elements_removes_the_header():
    decomposition = build()
    keep = {i for i in decomposition.ids if not i.startswith("header:accept[")}
    assert decomposition.rebuild(keep).header_value("accept") is None


def test_dropping_some_elements_rejoins_the_rest():
    decomposition = build()
    keep = set(decomposition.ids) - {"header:accept[1]"}
    assert decomposition.rebuild(keep).header_value("accept") == "application/json, */*"


def test_dropping_a_cookie_keeps_the_others():
    decomposition = build()
    keep = set(decomposition.ids) - {"cookie:theme"}
    assert [p.name for p in decomposition.rebuild(keep).cookies] == ["sid"]


def test_dropping_all_cookies_removes_the_header():
    decomposition = build()
    keep = {i for i in decomposition.ids if not i.startswith("cookie:")}
    assert decomposition.rebuild(keep).header_value("cookie") is None


def test_json_body_key_removal():
    decomposition = build()
    keep = set(decomposition.ids) - {"json:$.opts"}
    assert decomposition.rebuild(keep).body.data == {"id": 7}


def test_body_depth_controls_nesting():
    request = parse_curl(COMMAND, implicit_headers=False)
    assert "json:$.opts.deep" not in decompose(request, body_depth=1).ids
    assert "json:$.opts.deep" in decompose(request, body_depth=0).ids


def test_list_arrays_shift_safely():
    request = parse_curl(
        """curl https://e.test/ --data-raw '{"t":["a","b","c"]}'""", implicit_headers=False
    )
    decomposition = decompose(request, body_depth=0)
    keep = set(decomposition.ids) - {"json:$.t[1]"}
    assert decomposition.rebuild(keep).body.data == {"t": ["a", "c"]}


def test_select_matches_by_pattern():
    decomposition = build()
    assert decomposition.select(["cookie:*"]) == {"cookie:sid", "cookie:theme"}
    assert decomposition.select(["header:x-key"]) == {"header:x-key"}


def test_no_body_skips_body_atoms():
    assert not [a for a in decompose(parse_curl(COMMAND), include_body=False).atoms
                if a.kind == "json"]
