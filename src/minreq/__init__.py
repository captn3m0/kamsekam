"""minreq — delta-debug an HTTP request down to the smallest version that works."""

from __future__ import annotations

from minreq.atoms import Atom, Decomposition, decompose
from minreq.emit import FORMATS, render
from minreq.interop import from_httpx2, to_httpx2
from minreq.minimizer import Minimizer, Result, Stats, minimise, minimize
from minreq.model import Body, HttpRequest, Pair
from minreq.oracle import Oracle, Signature
from minreq.parse import load, parse_bru, parse_curl, parse_har
from minreq.transport import Response, Transport, build_transport

__version__ = "0.1.0"

__all__ = [
    "FORMATS",
    "Atom",
    "Body",
    "Decomposition",
    "HttpRequest",
    "Minimizer",
    "Oracle",
    "Pair",
    "Response",
    "Result",
    "Signature",
    "Stats",
    "Transport",
    "__version__",
    "build_transport",
    "decompose",
    "from_httpx2",
    "load",
    "minimise",
    "minimize",
    "parse_bru",
    "parse_curl",
    "parse_har",
    "render",
    "to_httpx2",
]
