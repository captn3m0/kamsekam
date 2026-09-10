"""Weighted delta debugging (WDD) over the removable atoms of a request."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kamsekam.atoms import Atom, Decomposition, decompose
from kamsekam.model import HttpRequest
from kamsekam.oracle import DEFAULT_MATCH, Oracle, Signature

if TYPE_CHECKING:
    import httpx2

Progress = Callable[[str, int, int], None]


@dataclass
class Stats:
    tests: int = 0
    cache_hits: int = 0
    requests_made: int = 0
    rounds: int = 0
    transport_failures: int = 0


@dataclass
class Result:
    """What survived, what went, and what it cost to find out."""

    original: HttpRequest
    request: HttpRequest
    baseline: Signature
    kept: list[Atom] = field(default_factory=list)
    removed: list[Atom] = field(default_factory=list)
    pinned: list[Atom] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)
    impersonate: str | None = None

    @property
    def bytes_saved(self) -> int:
        return self.original.size() - self.request.size()

    def as_httpx2(self) -> httpx2.Request:
        """The minimised request as a typed httpx2.Request."""
        from kamsekam.interop import to_httpx2

        return to_httpx2(self.request)

    def original_as_httpx2(self) -> httpx2.Request:
        from kamsekam.interop import to_httpx2

        return to_httpx2(self.original)

    def to_curl(self) -> str:
        return self.request.to_curl(impersonate=self.impersonate)


def split_evenly(items: Sequence[str], n: int) -> list[list[str]]:
    if n <= 1:
        return [list(items)]
    size, extra = divmod(len(items), n)
    out: list[list[str]] = []
    start = 0
    for i in range(n):
        stop = start + size + (1 if i < extra else 0)
        if stop > start:
            out.append(list(items[start:stop]))
        start = stop
    return out


def split_by_weight(items: Sequence[str], n: int, weights: dict[str, int]) -> list[list[str]]:
    """Partition into n chunks of roughly equal total weight, preserving order."""
    if n <= 1:
        return [list(items)]
    total = sum(weights.get(i, 1) for i in items)
    if total <= 0:
        return split_evenly(items, n)
    target = total / n
    out: list[list[str]] = []
    current: list[str] = []
    carried = 0.0
    for index, item in enumerate(items):
        current.append(item)
        carried += weights.get(item, 1)
        remaining = len(items) - index - 1
        if carried >= target and len(out) < n - 1 and remaining >= n - len(out) - 1:
            out.append(current)
            current = []
            carried = 0.0
    if current:
        out.append(current)
    return out if len(out) > 1 else split_evenly(items, n)


class Minimizer:
    """Zeller's ddmin, with partitions balanced by atom weight rather than count."""

    def __init__(
        self,
        decomposition: Decomposition,
        oracle: Oracle,
        pinned: set[str] | None = None,
        sweep: bool = True,
        progress: Progress | None = None,
    ):
        self.decomposition = decomposition
        self.oracle = oracle
        self.pinned = pinned or set()
        self.sweep = sweep
        self.progress = progress
        self.weights = {a.id: max(a.weight, 1) for a in decomposition.atoms}
        self.cache: dict[frozenset[str], bool] = {}
        self.stats = Stats()

    def _test(self, candidate: Sequence[str]) -> bool:
        key = frozenset(candidate) | frozenset(self.pinned)
        cached = self.cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached
        verdict = self.oracle.test(self.decomposition.rebuild(key))
        self.cache[key] = verdict
        self.stats.tests += 1
        if self.progress:
            self.progress("testing", self.stats.tests, len(key))
        return verdict

    def run(self) -> Result:
        candidates = [a.id for a in self.decomposition.atoms if a.id not in self.pinned]
        kept = self._wdd(candidates)
        if self.sweep:
            kept = self._sweep(kept)

        enabled = set(kept) | set(self.pinned)
        order = {a.id: i for i, a in enumerate(self.decomposition.atoms)}
        surviving = [a for a in self.decomposition.atoms if a.id in enabled]
        removed = sorted(
            (a for a in self.decomposition.atoms if a.id not in enabled),
            key=lambda a: order[a.id],
        )
        self.stats.requests_made = self.oracle.transport.count
        self.stats.transport_failures = self.oracle.failures
        return Result(
            original=self.decomposition.request,
            request=self.decomposition.rebuild(enabled),
            baseline=self.oracle.baseline,
            kept=[a for a in surviving if a.id not in self.pinned],
            removed=removed,
            pinned=[a for a in surviving if a.id in self.pinned],
            stats=self.stats,
        )

    def _wdd(self, items: Sequence[str]) -> list[str]:
        current = list(items)
        n = 2
        while len(current) >= 2:
            self.stats.rounds += 1
            chunks = split_by_weight(current, min(n, len(current)), self.weights)
            reduced = self._try_chunks(chunks)
            if reduced is not None:
                current, n = reduced, 2
                continue
            reduced = self._try_complements(current, chunks)
            if reduced is not None:
                current = reduced
                n = max(n - 1, 2)
                continue
            if n >= len(current):
                break
            n = min(2 * n, len(current))
        return current

    def _try_chunks(self, chunks: list[list[str]]) -> list[str] | None:
        for chunk in chunks:
            if chunk and self._test(chunk):
                return chunk
        return None

    def _try_complements(self, current: list[str], chunks: list[list[str]]) -> list[str] | None:
        if len(chunks) < 2:
            return None
        for chunk in chunks:
            dropped = set(chunk)
            complement = [i for i in current if i not in dropped]
            if complement and self._test(complement):
                return complement
        return None

    def _sweep(self, items: Sequence[str]) -> list[str]:
        """Heaviest-first one-by-one pass, so the result is provably 1-minimal."""
        current = list(items)
        for atom_id in sorted(items, key=lambda i: -self.weights.get(i, 1)):
            if atom_id not in current:
                continue
            trial = [i for i in current if i != atom_id]
            if self._test(trial):
                current = trial
        return current


def _coerce(request: Any) -> HttpRequest:
    if isinstance(request, HttpRequest):
        return request
    if isinstance(request, str | list):
        from kamsekam.parse import load

        return load(request)
    from kamsekam.interop import from_httpx2

    return from_httpx2(request)


def minimise(
    request: httpx2.Request | HttpRequest | str | list[str],
    *,
    match: tuple[str, ...] = DEFAULT_MATCH,
    size_tolerance: float = 0.0,
    keep: Sequence[str] = (),
    impersonate: str | None = None,
    timeout: float = 30.0,
    retries: int = 1,
    delay: float = 0.0,
    split_lists: bool = True,
    include_body: bool = True,
    body_depth: int = 1,
    sweep: bool = True,
    progress: Progress | None = None,
) -> Result:
    """Minimise a request given as an httpx2.Request, a curl command, or the internal model."""
    from kamsekam.transport import build_transport

    internal = _coerce(request)
    decomposition = decompose(
        internal, split_lists=split_lists, include_body=include_body, body_depth=body_depth
    )
    transport = build_transport(
        impersonate=impersonate, timeout=timeout, retries=retries, delay=delay
    )
    try:
        full = decomposition.rebuild(set(decomposition.ids))
        baseline = Signature.of(transport.send(full))
        oracle = Oracle(transport, baseline, match, size_tolerance=size_tolerance)
        result = Minimizer(
            decomposition,
            oracle,
            pinned=set(decomposition.select(list(keep))),
            sweep=sweep,
            progress=progress,
        ).run()
        result.impersonate = impersonate
        return result
    finally:
        transport.close()


minimize = minimise
