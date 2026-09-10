"""Command line entry point: kamsekam -- curl ..."""

from __future__ import annotations

import argparse
import json as jsonlib
import os
import sys
from dataclasses import asdict
from pathlib import Path

from kamsekam import __version__
from kamsekam.archive import (
    ARCHIVE_METHODS,
    EntryOutcome,
    derive_output_path,
    load_document,
    minimise_document,
)
from kamsekam.atoms import Atom, Decomposition, decompose
from kamsekam.emit import FORMATS, render
from kamsekam.minimizer import Minimizer, Result
from kamsekam.model import HttpRequest
from kamsekam.oracle import DEFAULT_MATCH, MATCH_FIELDS, Oracle, Signature, parse_match
from kamsekam.parse import load, parse_har, read_source
from kamsekam.transport import Transport, TransportError, build_transport


class UnstableBaseline(RuntimeError):
    pass


class Printer:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self.colour = sys.stderr.isatty() and not os.environ.get("NO_COLOR")
        self._progress = False

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.colour else text

    def _emit(self, label: str, colour: str, text: str) -> None:
        self.clear()
        print(f"  {self.paint(label.ljust(9), colour)} {text}", file=sys.stderr)

    def line(self, label: str, text: str) -> None:
        if not self.quiet:
            self._emit(label, "1;36", text)

    def warn(self, text: str) -> None:
        self._emit("warning", "1;33", text)

    def error(self, text: str) -> None:
        self._emit("error", "1;31", text)

    def blank(self) -> None:
        if not self.quiet:
            self.clear()
            print(file=sys.stderr)

    def progress(self, text: str) -> None:
        if self.quiet or not self.colour:
            return
        self._progress = True
        print(f"\r\033[K  {self.paint('running', '1;36')}   {text}", end="", file=sys.stderr)

    def clear(self) -> None:
        if self._progress:
            print("\r\033[K", end="", file=sys.stderr)
            self._progress = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kamsekam",
        description="Delta-debug an HTTP request down to the smallest version that still works.",
        epilog="everything after -- is read as a curl command: kamsekam -- curl -H 'a: b' URL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", nargs="?", help="a .har / .bru / curl file, or - for stdin")
    parser.add_argument("--version", action="version", version=f"kamsekam {__version__}")

    out = parser.add_argument_group("output")
    out.add_argument("-f", "--format", default=None,
                     help=f"comma-separated output formats: {', '.join(FORMATS)}")
    out.add_argument("-o", "--output", help="write the result here instead of stdout")
    out.add_argument("--json", action="store_true", help="emit a machine-readable report")
    out.add_argument("--wrap", action="store_true", help="wrap the curl command over lines")
    out.add_argument("-q", "--quiet", action="store_true", help="suppress the progress report")

    net = parser.add_argument_group("network")
    net.add_argument("--impersonate", metavar="ID",
                     help="replay via curl_cffi with a browser TLS fingerprint, e.g. chrome131")
    net.add_argument("--timeout", type=float, default=30.0, metavar="SEC")
    net.add_argument("--delay", type=float, default=0.0, metavar="SEC",
                     help="pause between requests")
    net.add_argument("--retries", type=int, default=1, metavar="N",
                     help="retries per transport failure")

    ora = parser.add_argument_group("oracle")
    ora.add_argument("--match", default=",".join(DEFAULT_MATCH),
                     help=f"signature fields that must hold: {', '.join(MATCH_FIELDS)}")
    ora.add_argument("--size-tolerance", type=float, default=0.0, metavar="PCT",
                     help="allowed body size drift when matching on size")
    ora.add_argument("--baseline-runs", type=int, default=2, metavar="N",
                     help="baseline replays used to detect unstable responses")
    ora.add_argument("--strict", action="store_true",
                     help="fail instead of relaxing --match on an unstable baseline")

    red = parser.add_argument_group("reduction")
    red.add_argument("--keep", action="append", default=[], metavar="PATTERN",
                     help="never remove atoms matching this, e.g. header:authorization")
    red.add_argument("--drop", action="append", default=[], metavar="PATTERN",
                     help="only consider atoms matching this")
    red.add_argument("--no-sweep", action="store_true", help="skip the final one-by-one pass")
    red.add_argument("--no-body", action="store_true", help="never take the body apart")
    red.add_argument("--body-depth", type=int, default=1, metavar="N",
                     help="how deep to split a JSON body (0 = unlimited)")
    red.add_argument("--no-split-lists", action="store_true",
                     help="treat multi-value headers as single atoms")
    red.add_argument("--no-implicit-headers", action="store_true",
                     help="do not add the User-Agent and Accept curl would send")
    red.add_argument("--entry", default=None, metavar="N|SUBSTRING",
                     help="restrict a HAR archive to one entry")
    red.add_argument("--dry-run", action="store_true",
                     help="list the atoms and exit without sending anything")
    return parser


def split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1 :]
    return argv, []


def _normalise_curl_argv(tokens: list[str]) -> str | list[str]:
    if len(tokens) == 1 and (" " in tokens[0] or "\n" in tokens[0]):
        return tokens[0]
    return tokens


def _measure_baseline(
    transport: Transport, decomposition: Decomposition, runs: int, printer: Printer, label: str
) -> list[Signature]:
    request = decomposition.rebuild(set(decomposition.ids))
    signatures = []
    for index in range(max(1, runs)):
        printer.progress(f"{label}baseline run {index + 1}/{max(1, runs)}")
        signatures.append(Signature.of(transport.send(request)))
    printer.clear()
    return signatures


def _stabilise(
    signatures: list[Signature], match: tuple[str, ...], strict: bool, printer: Printer
) -> tuple[str, ...]:
    first = signatures[0]
    if all(s == first for s in signatures):
        return match
    if any(s.status != first.status or s.content_type != first.content_type for s in signatures):
        raise UnstableBaseline("status or content-type changed between identical runs")
    if strict:
        raise UnstableBaseline("response is not byte-stable and --strict was given")
    sizes = {s.size for s in signatures}
    if len(sizes) == 1 and "hash" in match:
        printer.warn("response body changes between runs; matching on size instead of hash")
        return (*tuple(f for f in match if f != "hash"), "size")
    printer.warn(
        f"response body size varies ({min(sizes)}..{max(sizes)}B); "
        "matching on status and type only — consider --match status,type,size "
        "--size-tolerance 5"
    )
    return tuple(f for f in match if f in ("status", "type"))


def _minimise(
    request: HttpRequest,
    args: argparse.Namespace,
    transport: Transport,
    printer: Printer,
    match: tuple[str, ...],
    label: str = "",
) -> Result:
    decomposition = decompose(
        request,
        split_lists=not args.no_split_lists,
        include_body=not args.no_body,
        body_depth=args.body_depth,
    )
    pinned = set(decomposition.select(args.keep))
    if args.drop:
        pinned |= {a.id for a in decomposition.atoms} - decomposition.select(args.drop)

    signatures = _measure_baseline(transport, decomposition, args.baseline_runs, printer, label)
    baseline = signatures[0]
    effective = _stabilise(signatures, match, args.strict, printer)
    oracle = Oracle(transport, baseline, effective, size_tolerance=args.size_tolerance)
    return Minimizer(
        decomposition,
        oracle,
        pinned=pinned,
        sweep=not args.no_sweep,
        progress=lambda _phase, tested, size: printer.progress(
            f"{label}test {tested} · {size} atoms still in"
        ),
    ).run()


def _atom_lines(result: Result, printer: Printer) -> None:
    if printer.quiet:
        return
    if result.pinned:
        printer.line("pinned", f"{len(result.pinned)} atom(s) held by --keep/--drop")
    printer.blank()
    if not result.kept and not result.pinned:
        print("  everything was removable", file=sys.stderr)
    for atom in result.pinned + result.kept:
        marker = printer.paint("keep", "1;32")
        print(f"    {marker}  {atom.id.ljust(28)} {atom.label}", file=sys.stderr)
    if result.removed:
        names = ", ".join(a.id for a in result.removed)
        print(
            f"    {printer.paint('drop', '2;37')}  {len(result.removed)} removed: {names}",
            file=sys.stderr,
        )
    printer.blank()


def _atom_json(atom: Atom) -> dict[str, str]:
    return {"id": atom.id, "kind": atom.kind, "label": atom.label}


def _result_json(result: Result, match: tuple[str, ...]) -> dict[str, object]:
    stats = asdict(result.stats)
    stats["requests"] = stats.pop("requests_made")
    stats["bytes_saved"] = result.bytes_saved
    return {
        "baseline": {
            "status": result.baseline.status,
            "content_type": result.baseline.content_type,
            "size": result.baseline.size,
            "sha256": result.baseline.digest,
            "match": list(match),
        },
        "atoms": {
            "total": len(result.kept) + len(result.removed) + len(result.pinned),
            "kept": [_atom_json(a) for a in result.kept],
            "pinned": [a.id for a in result.pinned],
            "removed": [_atom_json(a) for a in result.removed],
        },
        "stats": stats,
    }


def _report_json(result: Result, rendered: dict[str, str], match: tuple[str, ...]) -> str:
    return jsonlib.dumps(_result_json(result, match) | {"output": rendered}, indent=2)


def _resolve_entry(raw: str | None) -> int | str | None:
    if raw is None:
        return None
    return int(raw) if raw.lstrip("-").isdigit() else raw


def _run_archive(
    text: str,
    path: Path | None,
    args: argparse.Namespace,
    printer: Printer,
    match: tuple[str, ...],
) -> int:
    try:
        document = load_document(text)
    except ValueError as exc:
        printer.error(str(exc))
        return 2

    total = len(document["log"]["entries"])
    printer.line("archive", f"{total} entries · minimising {'/'.join(ARCHIVE_METHODS)} only")

    transport = build_transport(
        impersonate=args.impersonate,
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
    )

    def minimise_entry(request: HttpRequest, index: int) -> Result:
        return _minimise(request, args, transport, printer, match, label=f"entry {index} · ")

    try:
        document, outcomes = minimise_document(
            document, minimise_entry, only=_resolve_entry(args.entry)
        )
    finally:
        transport.close()
    printer.clear()

    done = [o for o in outcomes if o.state == "minimised"]
    failed = [o for o in outcomes if o.state == "failed"]
    for outcome in done:
        assert outcome.result is not None
        result = outcome.result
        kept = len(result.kept) + len(result.pinned)
        printer.line(
            f"entry {outcome.index}",
            f"{outcome.method} {_short(outcome.url)} · "
            f"{kept + len(result.removed)} atoms → {kept} · {result.bytes_saved} bytes saved",
        )
    for outcome in failed:
        printer.warn(f"entry {outcome.index} {_short(outcome.url)}: {outcome.detail}")

    destination = Path(args.output).expanduser() if args.output else derive_output_path(
        path or "archive.har"
    )
    destination.write_text(jsonlib.dumps(document, indent=2) + "\n")

    saved = sum(o.result.bytes_saved for o in done if o.result)
    printer.line(
        "minimised",
        f"{len(done)} of {total} entries · {len(outcomes) - len(done) - len(failed)} skipped · "
        f"{saved} bytes saved",
    )
    printer.line("written", str(destination))
    if args.json:
        print(jsonlib.dumps(_archive_json(outcomes, destination, match), indent=2))
    return 0


def _archive_json(
    outcomes: list[EntryOutcome], destination: Path, match: tuple[str, ...]
) -> dict[str, object]:
    return {
        "output": str(destination),
        "entries": [
            {"index": o.index, "method": o.method, "url": o.url,
             "state": o.state, "detail": o.detail}
            | (_result_json(o.result, match) if o.result is not None else {})
            for o in outcomes
        ],
    }


def _short(url: str, limit: int = 60) -> str:
    return url if len(url) <= limit else url[: limit - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    own, curl_tokens = split_argv(raw)
    parser = build_parser()
    args = parser.parse_args(own)
    printer = Printer(quiet=args.quiet or args.json)

    if not curl_tokens and not args.input:
        parser.print_help(sys.stderr)
        return 2

    try:
        match = parse_match(args.match)
    except ValueError as exc:
        printer.error(str(exc))
        return 2

    source = _normalise_curl_argv(curl_tokens) if curl_tokens else args.input
    assert source is not None
    try:
        resolved, kind, path = read_source(source)
    except OSError as exc:
        printer.error(f"could not read the request: {exc}")
        return 2

    default_format = "har" if kind == "har" and args.entry is None else "curl"
    formats = [f.strip() for f in (args.format or default_format).split(",") if f.strip()]
    unknown = [f for f in formats if f not in FORMATS]
    if unknown:
        printer.error(f"unknown format(s): {', '.join(unknown)}")
        return 2

    if kind == "har" and "har" in formats and not args.dry_run:
        assert isinstance(resolved, str)
        return _run_archive(resolved, path, args, printer, match)

    try:
        request = (
            parse_har(resolved, entry=_resolve_entry(args.entry) or 0)
            if kind == "har" and isinstance(resolved, str)
            else load(resolved, implicit_headers=not args.no_implicit_headers)
        )
    except (ValueError, OSError) as exc:
        printer.error(f"could not read the request: {exc}")
        return 2

    decomposition = decompose(
        request,
        split_lists=not args.no_split_lists,
        include_body=not args.no_body,
        body_depth=args.body_depth,
    )
    printer.line(
        "input",
        f"{request.method} {request.base_url} · {len(decomposition)} atoms · "
        f"{request.size()} bytes",
    )
    if args.dry_run:
        for atom in decomposition.atoms:
            print(f"    {atom.id.ljust(30)} {atom.kind.ljust(15)} {atom.label}")
        return 0

    transport = build_transport(
        impersonate=args.impersonate,
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
    )
    try:
        result = _minimise(request, args, transport, printer, match)
    except TransportError as exc:
        printer.error(f"the original request could not be sent: {exc}")
        return 1
    except UnstableBaseline as exc:
        printer.error(f"baseline is unstable: {exc}")
        return 1
    finally:
        transport.close()
    printer.clear()

    printer.line("baseline", str(result.baseline))
    if result.baseline.status >= 400:
        printer.warn(
            f"baseline responded {result.baseline.status}; minimising towards an error response"
        )
    printer.line(
        "minimised",
        f"{result.stats.requests_made} requests · {len(decomposition)} atoms → "
        f"{len(result.kept) + len(result.pinned)} · {result.bytes_saved} bytes saved",
    )
    if result.stats.transport_failures:
        printer.warn(
            f"{result.stats.transport_failures} request(s) failed outright and were "
            "treated as 'still needed'"
        )
    _atom_lines(result, printer)

    rendered = {
        fmt: render(
            fmt, result.request, signature=result.baseline,
            impersonate=args.impersonate, wrap=args.wrap,
        )
        for fmt in formats
    }
    text = (
        _report_json(result, rendered, match)
        if args.json
        else "\n\n".join(rendered[f] for f in formats)
    )
    if args.output:
        Path(args.output).expanduser().write_text(text + "\n")
        printer.line("written", args.output)
    else:
        print(text)
    return 0
