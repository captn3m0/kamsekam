"""Decide whether a candidate request still produces the response we started with."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from minreq.model import HttpRequest
from minreq.transport import Response, Transport, TransportError

MATCH_FIELDS = ("status", "type", "size", "hash")
DEFAULT_MATCH = ("status", "type", "hash")


@dataclass(frozen=True)
class Signature:
    """The response tuple: code, type, body size, body hash."""

    status: int
    content_type: str
    size: int
    digest: str

    @classmethod
    def of(cls, response: Response) -> Signature:
        return cls(
            status=response.status,
            content_type=response.content_type.split(";")[0].strip().lower(),
            size=len(response.body),
            digest=hashlib.sha256(response.body).hexdigest(),
        )

    def __str__(self) -> str:
        kind = self.content_type or "-"
        return f"{self.status} {kind} {self.size}B sha256:{self.digest[:12]}"


def parse_match(spec: str) -> tuple[str, ...]:
    fields = tuple(f.strip().lower() for f in spec.split(",") if f.strip())
    unknown = [f for f in fields if f not in MATCH_FIELDS]
    if unknown:
        raise ValueError(f"unknown --match field(s): {', '.join(unknown)}")
    if not fields:
        raise ValueError("--match needs at least one field")
    return fields


class Oracle:
    """Wraps a transport with the comparison that drives delta debugging."""

    def __init__(
        self,
        transport: Transport,
        baseline: Signature,
        match: tuple[str, ...] = DEFAULT_MATCH,
        size_tolerance: float = 0.0,
    ):
        self.transport = transport
        self.baseline = baseline
        self.match = match
        self.size_tolerance = size_tolerance
        self.failures = 0

    def compare(self, candidate: Signature) -> bool:
        if "status" in self.match and candidate.status != self.baseline.status:
            return False
        if "type" in self.match and candidate.content_type != self.baseline.content_type:
            return False
        if "hash" in self.match and candidate.digest != self.baseline.digest:
            return False
        return not ("size" in self.match and not self._size_ok(candidate.size))

    def _size_ok(self, size: int) -> bool:
        if self.size_tolerance <= 0:
            return size == self.baseline.size
        allowed = max(1.0, self.baseline.size * self.size_tolerance / 100.0)
        return abs(size - self.baseline.size) <= allowed

    def test(self, request: HttpRequest) -> bool:
        try:
            response = self.transport.send(request)
        except TransportError:
            self.failures += 1
            return False
        return self.compare(Signature.of(response))
