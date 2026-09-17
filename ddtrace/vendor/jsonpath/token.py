"""JSONPath tokens."""

import sys
from typing import Tuple

# Utility tokens
TOKEN_EOF = sys.intern("TOKEN_EOF")
TOKEN_WHITESPACE = sys.intern("TOKEN_WHITESPACE")
TOKEN_ERROR = sys.intern("TOKEN_ERROR")

# JSONPath expression tokens
TOKEN_COLON = sys.intern("TOKEN_COLON")
TOKEN_COMMA = sys.intern("TOKEN_COMMA")
TOKEN_DDOT = sys.intern("TOKEN_DDOT")
TOKEN_DOT = sys.intern("TOKEN_DOT")
TOKEN_FILTER = sys.intern("TOKEN_FILTER")
TOKEN_KEY = sys.intern("TOKEN_KEY")
TOKEN_KEYS = sys.intern("TOKEN_KEYS")
TOKEN_KEYS_FILTER = sys.intern("TOKEN_KEYS_FILTER")
TOKEN_LBRACKET = sys.intern("TOKEN_LBRACKET")
TOKEN_PSEUDO_ROOT = sys.intern("TOKEN_PSEUDO_ROOT")
TOKEN_RBRACKET = sys.intern("TOKEN_RBRACKET")
TOKEN_ROOT = sys.intern("TOKEN_ROOT")
TOKEN_WILD = sys.intern("TOKEN_WILD")
TOKEN_NAME = sys.intern("TOKEN_NAME")
TOKEN_DOT_PROPERTY = sys.intern("TOKEN_DOT_PROPERTY")
TOKEN_DOT_KEY_PROPERTY = sys.intern("TOKEN_DOT_KEY_PROPERTY")
TOKEN_KEY_NAME = sys.intern("TOKEN_KEY_NAME")

# Filter expression tokens
TOKEN_AND = sys.intern("TOKEN_AND")
TOKEN_BLANK = sys.intern("TOKEN_BLANK")
TOKEN_CONTAINS = sys.intern("TOKEN_CONTAINS")
TOKEN_DOUBLE_QUOTE_STRING = sys.intern("TOKEN_DOUBLE_QUOTE_STRING")
TOKEN_EMPTY = sys.intern("TOKEN_EMPTY")
TOKEN_EQ = sys.intern("TOKEN_EQ")
TOKEN_FALSE = sys.intern("TOKEN_FALSE")
TOKEN_FILTER_CONTEXT = sys.intern("TOKEN_FILTER_CONTEXT")
TOKEN_FLOAT = sys.intern("TOKEN_FLOAT")
TOKEN_FUNCTION = sys.intern("TOKEN_FUNCTION")
TOKEN_GE = sys.intern("TOKEN_GE")
TOKEN_GT = sys.intern("TOKEN_GT")
TOKEN_IN = sys.intern("TOKEN_IN")
TOKEN_INT = sys.intern("TOKEN_INT")
TOKEN_LE = sys.intern("TOKEN_LE")
TOKEN_LG = sys.intern("TOKEN_LG")
TOKEN_LPAREN = sys.intern("TOKEN_LPAREN")
TOKEN_LT = sys.intern("TOKEN_LT")
TOKEN_MISSING = sys.intern("TOKEN_MISSING")
TOKEN_NE = sys.intern("TOKEN_NE")
TOKEN_NIL = sys.intern("TOKEN_NIL")
TOKEN_NONE = sys.intern("TOKEN_NONE")
TOKEN_NOT = sys.intern("TOKEN_NOT")
TOKEN_NULL = sys.intern("TOKEN_NULL")
TOKEN_OP = sys.intern("TOKEN_OP")
TOKEN_OR = sys.intern("TOKEN_OR")
TOKEN_RE = sys.intern("TOKEN_RE")
TOKEN_RE_FLAGS = sys.intern("TOKEN_RE_FLAGS")
TOKEN_RE_PATTERN = sys.intern("TOKEN_RE_PATTERN")
TOKEN_RPAREN = sys.intern("TOKEN_RPAREN")
TOKEN_SELF = sys.intern("TOKEN_SELF")
TOKEN_SINGLE_QUOTE_STRING = sys.intern("TOKEN_SINGLE_QUOTE_STRING")
TOKEN_STRING = sys.intern("TOKEN_STRING")
TOKEN_TRUE = sys.intern("TOKEN_TRUE")
TOKEN_UNDEFINED = sys.intern("TOKEN_UNDEFINED")

# Extension tokens
TOKEN_INTERSECTION = sys.intern("TOKEN_INTERSECTION")
TOKEN_UNION = sys.intern("TOKEN_UNION")


class Token:
    """A token, as returned from `lex.Lexer.tokenize()`.

    Attributes:
        kind (str): The token's type. It is always one of the constants defined
            in _jsonpath.token.py_.
        value (str): The _path_ substring containing text for the token.
        index (str): The index at which _value_ starts in _path_.
        path (str): A reference to the complete JSONPath string from which this
            token derives.
    """

    __slots__ = ("kind", "value", "index", "path")

    def __init__(
        self,
        kind: str,
        value: str,
        index: int,
        path: str,
    ) -> None:
        self.kind = kind
        self.value = value
        self.index = index
        self.path = path

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Token(kind={self.kind}, value={self.value!r}, "
            f"index={self.index}, path={self.path!r})"
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Token)
            and self.kind == other.kind
            and self.value == other.value
            and self.index == other.index
            and self.path == other.path
        )

    def __hash__(self) -> int:
        return hash((self.kind, self.value, self.index, self.path))

    def position(self) -> Tuple[int, int]:
        """Return the line and column number for the start of this token."""
        line_number = self.value.count("\n", 0, self.index) + 1
        column_number = self.index - self.value.rfind("\n", 0, self.index)
        return (line_number, column_number - 1)
