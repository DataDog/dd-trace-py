"""Step through a stream of tokens."""

from __future__ import annotations

from typing import Iterable

from .exceptions import JSONPathSyntaxError
from .token import TOKEN_EOF
from .token import TOKEN_WHITESPACE
from .token import Token


class TokenStream:
    """Step through a stream of tokens."""

    def __init__(self, token_iter: Iterable[Token]):
        self.tokens = list(token_iter)
        self.pos = 0
        path = self.tokens[0].path if self.tokens else ""
        self.eof = Token(TOKEN_EOF, "", -1, path)

    def __str__(self) -> str:  # pragma: no cover
        return f"current: {self.current}\nnext: {self.peek}"

    def current(self) -> Token:
        """Return the token at the current position in the stream."""
        try:
            return self.tokens[self.pos]
        except IndexError:
            return self.eof

    def next(self) -> Token:
        """Return the token at the current position and advance the pointer."""
        try:
            token = self.tokens[self.pos]
            self.pos += 1
            return token
        except IndexError:
            return self.eof

    def peek(self, offset: int = 1) -> Token:
        """Return the token at current position plus the offset.

        Does not advance the pointer.
        """
        try:
            return self.tokens[self.pos + offset]
        except IndexError:
            return self.eof

    def eat(self, kind: str, message: str | None = None) -> Token:
        """Assert tge type if the current token and advance the pointer."""
        token = self.next()
        if token.kind != kind:
            raise JSONPathSyntaxError(
                message or f"expected {kind}, found {token.kind!r}",
                token=token,
            )
        return token

    def expect(self, *typ: str) -> None:
        """Raise an exception of the current token is not in `typ`."""
        token = self.current()
        if token.kind not in typ:
            if len(typ) == 1:
                _typ = repr(typ[0])
            else:
                _typ = f"one of {typ!r}"
            raise JSONPathSyntaxError(
                f"expected {_typ}, found {token.kind!r}",
                token=token,
            )

    def expect_peek(self, *typ: str) -> None:
        """Raise an exception of the current token is not in `typ`."""
        token = self.peek()
        if token.kind not in typ:
            if len(typ) == 1:
                _typ = repr(typ[0])
            else:
                _typ = f"one of {typ!r}"
            raise JSONPathSyntaxError(
                f"expected {_typ}, found {token.kind!r}",
                token=token,
            )

    def expect_peek_not(self, typ: str, message: str) -> None:
        """Raise an exception if the next token kind of _typ_."""
        if self.peek().kind == typ:
            raise JSONPathSyntaxError(message, token=self.peek())

    def skip_whitespace(self) -> bool:
        """Skip whitespace."""
        if self.current().kind == TOKEN_WHITESPACE:
            self.pos += 1
            return True
        return False
