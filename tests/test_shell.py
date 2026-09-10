import pytest

from minreq.parse.shell import ShellSyntaxError, tokenize


def test_single_and_double_quotes():
    assert tokenize("""curl 'a b' "c d" e""") == ["curl", "a b", "c d", "e"]


def test_line_continuations():
    assert tokenize("curl \\\n  -H 'a: b' \\\n  url") == ["curl", "-H", "a: b", "url"]


def test_ansi_c_quoting():
    tokens = tokenize(r"""-H $'sec-ch-ua: "Chromium";v="128", "Not;A=Brand";v="99"'""")
    assert tokens[1] == 'sec-ch-ua: "Chromium";v="128", "Not;A=Brand";v="99"'
    assert tokenize(r"-H $'x: \'inner\''")[1] == "x: 'inner'"


def test_ansi_c_escapes():
    assert tokenize(r"$'a\tb\nc\x41B\\d'") == ["a\tb\nc\x41B\\d"]


def test_escaped_characters_outside_quotes():
    assert tokenize(r"a\ b c") == ["a b", "c"]


def test_double_quote_escapes():
    assert tokenize(r'"he said \"hi\" and \\ done"') == ['he said "hi" and \\ done']


def test_windows_caret_continuation():
    assert tokenize("curl ^\n  url") == ["curl", "url"]


def test_adjacent_quoting_concatenates():
    assert tokenize("'a'\"b\"c") == ["abc"]


def test_unterminated_quote_raises():
    with pytest.raises(ShellSyntaxError):
        tokenize("curl 'unterminated")
