r"""Replace `\uXXXX` escape sequences with Unicode code points."""

from typing import List
from typing import Tuple

from .exceptions import JSONPathSyntaxError
from .token import Token


def unescape_string(value: str, token: Token, quote: str) -> str:
    """Return `value` with escape sequences replaced with Unicode code points."""
    unescaped: List[str] = []
    index = 0

    while index < len(value):
        ch = value[index]
        if ch == "\\":
            index += 1
            _ch, index = _decode_escape_sequence(value, index, token, quote)
            unescaped.append(_ch)
        else:
            _string_from_codepoint(ord(ch), token)
            unescaped.append(ch)
        index += 1
    return "".join(unescaped)


def _decode_escape_sequence(  # noqa: PLR0911
    value: str, index: int, token: Token, quote: str
) -> Tuple[str, int]:
    try:
        ch = value[index]
    except IndexError as err:
        raise JSONPathSyntaxError("incomplete escape sequence", token=token) from err

    if ch == quote:
        return quote, index
    if ch == "\\":
        return "\\", index
    if ch == "/":
        return "/", index
    if ch == "b":
        return "\x08", index
    if ch == "f":
        return "\x0c", index
    if ch == "n":
        return "\n", index
    if ch == "r":
        return "\r", index
    if ch == "t":
        return "\t", index
    if ch == "u":
        codepoint, index = _decode_hex_char(value, index, token)
        return _string_from_codepoint(codepoint, token), index

    raise JSONPathSyntaxError(
        f"unknown escape sequence at index {token.index + index - 1}",
        token=token,
    )


def _decode_hex_char(value: str, index: int, token: Token) -> Tuple[int, int]:
    length = len(value)

    if index + 4 >= length:
        raise JSONPathSyntaxError(
            f"incomplete escape sequence at index {token.index + index - 1}",
            token=token,
        )

    index += 1  # move past 'u'
    codepoint = _parse_hex_digits(value[index : index + 4], token)

    if _is_low_surrogate(codepoint):
        raise JSONPathSyntaxError(
            f"unexpected low surrogate at index {token.index + index - 1}",
            token=token,
        )

    if _is_high_surrogate(codepoint):
        # expect a surrogate pair
        if not (
            index + 9 < length and value[index + 4] == "\\" and value[index + 5] == "u"
        ):
            raise JSONPathSyntaxError(
                f"incomplete escape sequence at index {token.index + index - 2}",
                token=token,
            )

        low_surrogate = _parse_hex_digits(value[index + 6 : index + 10], token)

        if not _is_low_surrogate(low_surrogate):
            raise JSONPathSyntaxError(
                f"unexpected codepoint at index {token.index + index + 4}",
                token=token,
            )

        codepoint = 0x10000 + (((codepoint & 0x03FF) << 10) | (low_surrogate & 0x03FF))

        return (codepoint, index + 9)

    return (codepoint, index + 3)


def _parse_hex_digits(digits: str, token: Token) -> int:
    codepoint = 0
    for digit in digits.encode():
        codepoint <<= 4
        if digit >= 48 and digit <= 57:
            codepoint |= digit - 48
        elif digit >= 65 and digit <= 70:
            codepoint |= digit - 65 + 10
        elif digit >= 97 and digit <= 102:
            codepoint |= digit - 97 + 10
        else:
            raise JSONPathSyntaxError(
                "invalid \\uXXXX escape sequence",
                token=token,
            )
    return codepoint


def _string_from_codepoint(codepoint: int, token: Token) -> str:
    if codepoint <= 0x1F:
        raise JSONPathSyntaxError("invalid character", token=token)
    return chr(codepoint)


def _is_high_surrogate(codepoint: int) -> bool:
    return codepoint >= 0xD800 and codepoint <= 0xDBFF


def _is_low_surrogate(codepoint: int) -> bool:
    return codepoint >= 0xDC00 and codepoint <= 0xDFFF
