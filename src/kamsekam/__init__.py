"""kamsekam — delta-debug an HTTP request down to the smallest version that works."""

from __future__ import annotations

from kamsekam.atoms import Atom, Decomposition, decompose
from kamsekam.emit import FORMATS, render
from kamsekam.interop import from_httpx2, to_curl, to_httpx2, uncurl
from kamsekam.minimizer import Minimizer, Result, Stats, minimise, minimize
from kamsekam.model import Body, HttpRequest, Pair
from kamsekam.oracle import Oracle, Signature
from kamsekam.parse import load, parse_curl, parse_har
from kamsekam.transport import Response, Transport, build_transport

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
    "parse_curl",
    "parse_har",
    "render",
    "to_curl",
    "to_httpx2",
    "uncurl",
]
