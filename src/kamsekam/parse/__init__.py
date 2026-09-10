"""Detect and dispatch to the right input parser."""

from __future__ import annotations

import sys
from pathlib import Path

from kamsekam.model import HttpRequest
from kamsekam.parse.bru import BruParseError, parse_bru
from kamsekam.parse.curl import CurlParseError, parse_curl
from kamsekam.parse.har import HarParseError, list_entries, parse_har

__all__ = [
    "BruParseError",
    "CurlParseError",
    "HarParseError",
    "detect_format",
    "list_entries",
    "load",
    "parse_bru",
    "parse_curl",
    "parse_har",
    "read_source",
]


def detect_format(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("{") and '"log"' in stripped[:2000]:
        return "har"
    if stripped.startswith("curl") or "\ncurl " in stripped[:200]:
        return "curl"
    for block in ("meta {", "get {", "post {", "put {", "patch {", "delete {"):
        if block in stripped:
            return "bru"
    if stripped.startswith("{"):
        return "har"
    return "curl"


def read_source(
    source: str | list[str], kind: str | None = None
) -> tuple[str | list[str], str, Path | None]:
    """Resolve argv, a literal command, a file path or stdin into (text, kind, path)."""
    if isinstance(source, list):
        return source, "curl", None
    text = source
    path: Path | None = None
    if source == "-":
        text = sys.stdin.read()
    elif "\n" not in source and len(source) < 4096:
        candidate = Path(source).expanduser()
        try:
            if candidate.is_file():
                text = candidate.read_text()
                path = candidate
                kind = kind or _kind_from_suffix(candidate)
        except OSError:
            pass
    return text, kind or detect_format(text), path


def load(
    source: str | list[str],
    kind: str | None = None,
    entry: int | str = 0,
    implicit_headers: bool = True,
) -> HttpRequest:
    """Load a request from curl argv, a command string, a file path, or stdin."""
    resolved, kind, _ = read_source(source, kind)
    if isinstance(resolved, list):
        return parse_curl(resolved, implicit_headers=implicit_headers)
    text = resolved
    if kind == "har":
        return parse_har(text, entry=entry)
    if kind == "bru":
        return parse_bru(text)
    return parse_curl(text, implicit_headers=implicit_headers)


def _kind_from_suffix(path: Path) -> str | None:
    return {".har": "har", ".bru": "bru", ".json": "har"}.get(path.suffix.lower())
