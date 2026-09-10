from collections.abc import Callable
from itertools import chain

from minreq.atoms import decompose
from minreq.minimizer import Minimizer, split_by_weight, split_evenly
from minreq.model import HttpRequest
from minreq.oracle import Signature
from minreq.parse.curl import parse_curl

COMMAND = (
    "curl 'https://e.test/p?a=1&b=2&c=3' "
    "-H 'accept: application/json, text/plain, */*' "
    "-H 'x-key: secret' -H 'x-noise: 1' -H 'x-more-noise: 2' "
    "-b 'sid=1; theme=dark; _ga=9; consent=yes'"
)


class FakeTransport:
    def __init__(self) -> None:
        self.count = 0

    def close(self) -> None:
        pass


class FakeOracle:
    def __init__(self, predicate: Callable[[HttpRequest], bool]):
        self.transport = FakeTransport()
        self.baseline = Signature(200, "text/plain", 2, "ab")
        self.failures = 0
        self.predicate = predicate

    def test(self, request: HttpRequest) -> bool:
        self.transport.count += 1
        return self.predicate(request)


def needs_key_and_sid(request: HttpRequest) -> bool:
    has_cookie = any(p.name == "sid" for p in request.cookies)
    return request.header_value("x-key") == "secret" and has_cookie


def test_split_evenly_covers_everything():
    chunks = split_evenly(list("abcdefg"), 3)
    assert [len(c) for c in chunks] == [3, 2, 2]
    assert sorted(chain.from_iterable(chunks)) == list("abcdefg")


def test_split_by_weight_balances_on_bytes():
    items = ["heavy", "a", "b", "c"]
    weights = {"heavy": 300, "a": 1, "b": 1, "c": 1}
    chunks = split_by_weight(items, 2, weights)
    assert chunks[0] == ["heavy"]
    assert sorted(chain.from_iterable(chunks)) == sorted(items)


def test_split_by_weight_never_loses_items():
    items = [str(i) for i in range(9)]
    weights = {i: 1 for i in items}
    for n in range(2, 10):
        assert sorted(chain.from_iterable(split_by_weight(items, n, weights))) == sorted(items)


def run() -> tuple[set[str], int]:
    decomposition = decompose(parse_curl(COMMAND, implicit_headers=False))
    oracle = FakeOracle(needs_key_and_sid)
    result = Minimizer(decomposition, oracle).run()
    return {a.id for a in result.kept}, result.stats.requests_made


def test_finds_the_minimum():
    kept, _ = run()
    assert kept == {"header:x-key", "cookie:sid"}


def test_weighting_isolates_the_heavy_atom_early():
    heavy = COMMAND.replace("theme=dark", "theme=" + "x" * 400)
    decomposition = decompose(parse_curl(heavy, implicit_headers=False))
    oracle = FakeOracle(needs_key_and_sid)
    result = Minimizer(decomposition, oracle).run()
    assert {a.id for a in result.kept} == {"header:x-key", "cookie:sid"}
    assert result.bytes_saved > 400


def test_result_is_one_minimal():
    decomposition = decompose(parse_curl(COMMAND, implicit_headers=False))
    oracle = FakeOracle(needs_key_and_sid)
    result = Minimizer(decomposition, oracle).run()
    kept = {a.id for a in result.kept}
    for atom_id in kept:
        assert not needs_key_and_sid(decomposition.rebuild(kept - {atom_id}))


def test_pinned_atoms_are_never_removed():
    decomposition = decompose(parse_curl(COMMAND, implicit_headers=False))
    oracle = FakeOracle(needs_key_and_sid)
    result = Minimizer(decomposition, oracle, pinned={"cookie:theme"}).run()
    assert [a.id for a in result.pinned] == ["cookie:theme"]
    assert any(p.name == "theme" for p in result.request.cookies)
    assert {a.id for a in result.kept} == {"header:x-key", "cookie:sid"}


def test_caching_avoids_repeat_requests():
    decomposition = decompose(parse_curl(COMMAND, implicit_headers=False))
    oracle = FakeOracle(needs_key_and_sid)
    minimizer = Minimizer(decomposition, oracle)
    result = minimizer.run()
    assert result.stats.cache_hits > 0
    assert result.stats.requests_made == result.stats.tests


def test_nothing_removable_keeps_everything():
    decomposition = decompose(parse_curl(COMMAND, implicit_headers=False))
    full = decomposition.request.size()
    oracle = FakeOracle(lambda r: r.size() == full)
    result = Minimizer(decomposition, oracle).run()
    assert len(result.kept) == len(decomposition)


def test_bytes_saved_is_positive():
    decomposition = decompose(parse_curl(COMMAND, implicit_headers=False))
    result = Minimizer(decomposition, FakeOracle(needs_key_and_sid)).run()
    assert result.bytes_saved > 0
