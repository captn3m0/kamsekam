"""A shell tokenizer that survives what browsers actually put on the clipboard."""

from __future__ import annotations

_ANSI_C_SIMPLE = {
    "a": "\a",
    "b": "\b",
    "e": "\x1b",
    "E": "\x1b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "?": "?",
}

_DOUBLE_QUOTE_ESCAPES = set('"\\$`\n')

WHITESPACE = " \t\n\r\f\v"


class ShellSyntaxError(ValueError):
    pass


def _read_ansi_c(text: str, i: int) -> tuple[str, int]:
    out: list[str] = []
    while i < len(text):
        ch = text[i]
        if ch == "'":
            return "".join(out), i + 1
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(text):
            out.append("\\")
            break
        esc = text[i]
        if esc in _ANSI_C_SIMPLE:
            out.append(_ANSI_C_SIMPLE[esc])
            i += 1
        elif esc == "x":
            j = i + 1
            while j < len(text) and j < i + 3 and text[j] in "0123456789abcdefABCDEF":
                j += 1
            out.append(chr(int(text[i + 1 : j], 16)) if j > i + 1 else "x")
            i = j
        elif esc in ("u", "U"):
            width = 4 if esc == "u" else 8
            j = i + 1
            while j < len(text) and j < i + 1 + width and text[j] in "0123456789abcdefABCDEF":
                j += 1
            out.append(chr(int(text[i + 1 : j], 16)) if j > i + 1 else esc)
            i = j
        elif esc.isdigit():
            j = i
            while j < len(text) and j < i + 3 and text[j] in "01234567":
                j += 1
            out.append(chr(int(text[i:j], 8)))
            i = j
        else:
            out.append(esc)
            i += 1
    raise ShellSyntaxError("unterminated $'...' string")


def tokenize(text: str) -> list[str]:
    """Split a command line the way bash would, including $'..' and continuations."""
    tokens: list[str] = []
    buf: list[str] = []
    started = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in WHITESPACE:
            if started:
                tokens.append("".join(buf))
                buf = []
                started = False
            i += 1
        elif ch == "\\":
            if i + 1 < n and text[i + 1] == "\n":
                i += 2
            elif i + 2 < n and text[i + 1 : i + 3] == "\r\n":
                i += 3
            elif i + 1 < n:
                buf.append(text[i + 1])
                started = True
                i += 2
            else:
                i += 1
        elif ch == "^" and i + 1 < n and text[i + 1] in "\r\n":
            i += 2 if text[i + 1] == "\n" else 3
        elif ch == "'":
            end = text.find("'", i + 1)
            if end == -1:
                raise ShellSyntaxError("unterminated single quote")
            buf.append(text[i + 1 : end])
            started = True
            i = end + 1
        elif ch == "$" and i + 1 < n and text[i + 1] == "'":
            value, i = _read_ansi_c(text, i + 2)
            buf.append(value)
            started = True
        elif ch == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in _DOUBLE_QUOTE_ESCAPES:
                    if text[i + 1] != "\n":
                        buf.append(text[i + 1])
                    i += 2
                else:
                    buf.append(text[i])
                    i += 1
            if i >= n:
                raise ShellSyntaxError("unterminated double quote")
            started = True
            i += 1
        else:
            buf.append(ch)
            started = True
            i += 1
    if started:
        tokens.append("".join(buf))
    return tokens
