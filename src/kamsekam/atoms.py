"""Break a request into independently removable atoms, and put it back together."""

from __future__ import annotations

import json as jsonlib
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

from kamsekam.model import Body, HttpRequest, Pair, is_list_header, split_list_value

JsonPath = tuple[str | int, ...]


@dataclass(frozen=True)
class Atom:
    """One removable piece of a request."""

    id: str
    kind: str
    label: str
    weight: int

    def matches(self, pattern: str) -> bool:
        needle = pattern.lower()
        return fnmatch(self.id.lower(), needle) or fnmatch(self.id.lower(), f"{needle}*")


@dataclass
class _HeaderSlot:
    name: str
    value: str
    atom_id: str | None = None
    elements: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Decomposition:
    """The original request plus every atom that can be taken out of it."""

    request: HttpRequest
    atoms: list[Atom]
    _headers: list[_HeaderSlot]
    _query: list[tuple[str, Pair]]
    _cookies: list[tuple[str, Pair]]
    _fields: list[tuple[str, Pair]]
    _json: dict[JsonPath, str]
    _body_atom: str | None

    def __len__(self) -> int:
        return len(self.atoms)

    @property
    def ids(self) -> list[str]:
        return [a.id for a in self.atoms]

    def select(self, patterns: list[str]) -> set[str]:
        return {a.id for a in self.atoms for p in patterns if a.matches(p)}

    def rebuild(self, enabled: set[str] | frozenset[str]) -> HttpRequest:
        headers: list[tuple[str, str]] = []
        for slot in self._headers:
            if slot.atom_id is not None:
                if slot.atom_id in enabled:
                    headers.append((slot.name, slot.value))
                continue
            kept = [text for atom_id, text in slot.elements if atom_id in enabled]
            if kept:
                headers.append((slot.name, ", ".join(kept)))
        return HttpRequest(
            method=self.request.method,
            base_url=self.request.base_url,
            query=[p for atom_id, p in self._query if atom_id in enabled],
            headers=headers,
            cookies=[p for atom_id, p in self._cookies if atom_id in enabled],
            body=self._rebuild_body(enabled),
            follow_redirects=self.request.follow_redirects,
            insecure=self.request.insecure,
            http_version=self.request.http_version,
            proxy=self.request.proxy,
        )

    def _rebuild_body(self, enabled: set[str] | frozenset[str]) -> Body | None:
        original = self.request.body
        if original is None:
            return None
        if original.kind in ("form", "multipart"):
            kept = [p for atom_id, p in self._fields if atom_id in enabled]
            return Body(kind=original.kind, fields=kept)
        if original.kind == "json" and self._json:
            data = _filter_json(original.data, (), enabled, self._json)
            return Body(kind="json", data=data, raw=original.raw)
        if self._body_atom is not None and self._body_atom not in enabled:
            return None
        return original


def _filter_json(
    data: Any, path: JsonPath, enabled: set[str] | frozenset[str], index: dict[JsonPath, str]
) -> Any:
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            child = (*path, key)
            atom_id = index.get(child)
            if atom_id is not None and atom_id not in enabled:
                continue
            out[key] = _filter_json(value, child, enabled, index)
        return out
    if isinstance(data, list):
        out_list = []
        for position, value in enumerate(data):
            child = (*path, position)
            atom_id = index.get(child)
            if atom_id is not None and atom_id not in enabled:
                continue
            out_list.append(_filter_json(value, child, enabled, index))
        return out_list
    return data


class _IdFactory:
    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def make(self, base: str) -> str:
        count = self.seen.get(base, 0)
        self.seen[base] = count + 1
        return base if count == 0 else f"{base}#{count + 1}"


def _truncate(text: str, limit: int = 68) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def decompose(
    request: HttpRequest,
    split_lists: bool = True,
    include_body: bool = True,
    body_depth: int = 1,
) -> Decomposition:
    """Enumerate every atom of a request: headers, list elements, cookies, query, body."""
    ids = _IdFactory()
    atoms: list[Atom] = []
    header_slots: list[_HeaderSlot] = []

    for name, value in request.headers:
        lowered = name.lower()
        elements = split_list_value(value) if split_lists and is_list_header(name) else []
        if len(elements) > 1:
            slot = _HeaderSlot(name=name, value=value)
            for position, text in enumerate(elements):
                atom_id = ids.make(f"header:{lowered}[{position}]")
                slot.elements.append((atom_id, text))
                atoms.append(
                    Atom(atom_id, "header-element", f"{name}: {_truncate(text)}", len(text) + 2)
                )
            header_slots.append(slot)
        else:
            atom_id = ids.make(f"header:{lowered}")
            header_slots.append(_HeaderSlot(name=name, value=value, atom_id=atom_id))
            atoms.append(
                Atom(atom_id, "header", f"{name}: {_truncate(value)}", len(name) + len(value) + 4)
            )

    query_slots: list[tuple[str, Pair]] = []
    for pair in request.query:
        atom_id = ids.make(f"query:{pair.name}")
        query_slots.append((atom_id, pair))
        atoms.append(Atom(atom_id, "query", f"?{_truncate(pair.raw)}", len(pair.raw) + 1))

    cookie_slots: list[tuple[str, Pair]] = []
    for pair in request.cookies:
        atom_id = ids.make(f"cookie:{pair.name}")
        cookie_slots.append((atom_id, pair))
        atoms.append(Atom(atom_id, "cookie", _truncate(pair.raw), len(pair.raw) + 2))

    field_slots: list[tuple[str, Pair]] = []
    json_index: dict[JsonPath, str] = {}
    body_atom: str | None = None
    body = request.body
    if include_body and body is not None:
        if body.kind in ("form", "multipart"):
            for pair in body.fields:
                atom_id = ids.make(f"field:{pair.name}")
                field_slots.append((atom_id, pair))
                atoms.append(Atom(atom_id, "field", _truncate(pair.raw), len(pair.raw) + 1))
        elif body.kind == "json":
            _walk_json(body.data, (), 1, body_depth, ids, json_index, atoms)
        elif body.raw:
            body_atom = ids.make("body")
            atoms.append(
                Atom(body_atom, "body", f"body ({len(body.raw)} bytes)", len(body.raw))
            )

    return Decomposition(
        request=request,
        atoms=atoms,
        _headers=header_slots,
        _query=query_slots,
        _cookies=cookie_slots,
        _fields=field_slots,
        _json=json_index,
        _body_atom=body_atom,
    )


def _walk_json(
    data: Any,
    path: JsonPath,
    depth: int,
    max_depth: int,
    ids: _IdFactory,
    index: dict[JsonPath, str],
    atoms: list[Atom],
) -> None:
    if isinstance(data, dict):
        items: list[tuple[str | int, Any]] = list(data.items())
    elif isinstance(data, list):
        items = list(enumerate(data))
    else:
        return
    for key, value in items:
        child = (*path, key)
        atom_id = ids.make(f"json:{_render_path(child)}")
        index[child] = atom_id
        weight = len(jsonlib.dumps({str(key): value}, separators=(",", ":")))
        atoms.append(Atom(atom_id, "json", _truncate(f"{_render_path(child)} = {value!r}"), weight))
        if max_depth == 0 or depth < max_depth:
            _walk_json(value, child, depth + 1, max_depth, ids, index, atoms)


def _render_path(path: JsonPath) -> str:
    out = "$"
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out
