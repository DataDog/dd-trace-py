"""The default JSONPath parser."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Optional
from typing import Union

from .function_extensions.filter_function import ExpressionType
from .function_extensions.filter_function import FilterFunction

from .exceptions import JSONPathRecursionError
from .exceptions import JSONPathSyntaxError
from .exceptions import JSONPathTypeError
from .filter import CURRENT_KEY
from .filter import FALSE
from .filter import NIL
from .filter import TRUE
from .filter import UNDEFINED_LITERAL
from .filter import BaseExpression
from .filter import FilterContextPath
from .filter import FilterExpression
from .filter import FilterExpressionLiteral
from .filter import FilterQuery
from .filter import FloatLiteral
from .filter import FunctionExtension
from .filter import InfixExpression
from .filter import IntegerLiteral
from .filter import ListLiteral
from .filter import Nil
from .filter import PrefixExpression
from .filter import RegexLiteral
from .filter import RelativeFilterQuery
from .filter import RootFilterQuery
from .filter import StringLiteral
from .path import JSONPath
from .segments import JSONPathChildSegment
from .segments import JSONPathRecursiveDescentSegment
from .segments import JSONPathSegment
from .selectors import Filter
from .selectors import IndexSelector
from .selectors import JSONPathSelector
from .selectors import KeySelector
from .selectors import KeysFilter
from .selectors import KeysSelector
from .selectors import NameSelector
from .selectors import SingularQuerySelector
from .selectors import SliceSelector
from .selectors import WildcardSelector
from .token import TOKEN_AND
from .token import TOKEN_COLON
from .token import TOKEN_COMMA
from .token import TOKEN_CONTAINS
from .token import TOKEN_DDOT
from .token import TOKEN_DOT
from .token import TOKEN_DOUBLE_QUOTE_STRING
from .token import TOKEN_EOF
from .token import TOKEN_EQ
from .token import TOKEN_FALSE
from .token import TOKEN_FILTER
from .token import TOKEN_FILTER_CONTEXT
from .token import TOKEN_FLOAT
from .token import TOKEN_FUNCTION
from .token import TOKEN_GE
from .token import TOKEN_GT
from .token import TOKEN_IN
from .token import TOKEN_INT
from .token import TOKEN_INTERSECTION
from .token import TOKEN_KEY
from .token import TOKEN_KEY_NAME
from .token import TOKEN_KEYS
from .token import TOKEN_KEYS_FILTER
from .token import TOKEN_LBRACKET
from .token import TOKEN_LE
from .token import TOKEN_LG
from .token import TOKEN_LPAREN
from .token import TOKEN_LT
from .token import TOKEN_MISSING
from .token import TOKEN_NAME
from .token import TOKEN_NE
from .token import TOKEN_NIL
from .token import TOKEN_NONE
from .token import TOKEN_NOT
from .token import TOKEN_NULL
from .token import TOKEN_OR
from .token import TOKEN_PSEUDO_ROOT
from .token import TOKEN_RBRACKET
from .token import TOKEN_RE
from .token import TOKEN_RE_FLAGS
from .token import TOKEN_RE_PATTERN
from .token import TOKEN_ROOT
from .token import TOKEN_RPAREN
from .token import TOKEN_SELF
from .token import TOKEN_SINGLE_QUOTE_STRING
from .token import TOKEN_TRUE
from .token import TOKEN_UNDEFINED
from .token import TOKEN_UNION
from .token import TOKEN_WHITESPACE
from .token import TOKEN_WILD
from .token import Token
from .unescape import unescape_string

if TYPE_CHECKING:
    from .env import JSONPathEnvironment
    from .stream import TokenStream

# ruff: noqa: D102

INVALID_NAME_SELECTOR_CHARS = [
    "\x00",
    "\x01",
    "\x02",
    "\x03",
    "\x04",
    "\x05",
    "\x06",
    "\x07",
    "\x08",
    "\t",
    "\n",
    "\x0b",
    "\x0c",
    "\r",
    "\x0e",
    "\x0f",
    "\x10",
    "\x11",
    "\x12",
    "\x13",
    "\x14",
    "\x15",
    "\x16",
    "\x17",
    "\x18",
    "\x19",
    "\x1a",
    "\x1b",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x1f",
]


class Parser:
    """A JSONPath parser bound to a JSONPathEnvironment."""

    PRECEDENCE_LOWEST = 1
    PRECEDENCE_LOGICAL_OR = 3
    PRECEDENCE_LOGICAL_AND = 4
    PRECEDENCE_RELATIONAL = 5
    PRECEDENCE_MEMBERSHIP = 6
    PRECEDENCE_PREFIX = 7

    PRECEDENCES = {
        TOKEN_AND: PRECEDENCE_LOGICAL_AND,
        TOKEN_CONTAINS: PRECEDENCE_MEMBERSHIP,
        TOKEN_EQ: PRECEDENCE_RELATIONAL,
        TOKEN_GE: PRECEDENCE_RELATIONAL,
        TOKEN_GT: PRECEDENCE_RELATIONAL,
        TOKEN_IN: PRECEDENCE_MEMBERSHIP,
        TOKEN_LE: PRECEDENCE_RELATIONAL,
        TOKEN_LG: PRECEDENCE_RELATIONAL,
        TOKEN_LT: PRECEDENCE_RELATIONAL,
        TOKEN_NE: PRECEDENCE_RELATIONAL,
        TOKEN_NOT: PRECEDENCE_PREFIX,
        TOKEN_OR: PRECEDENCE_LOGICAL_OR,
        TOKEN_RE: PRECEDENCE_RELATIONAL,
        TOKEN_RPAREN: PRECEDENCE_LOWEST,
    }

    # Mapping of operator token to canonical string.
    BINARY_OPERATORS = {
        TOKEN_AND: "&&",
        TOKEN_CONTAINS: "contains",
        TOKEN_EQ: "==",
        TOKEN_GE: ">=",
        TOKEN_GT: ">",
        TOKEN_IN: "in",
        TOKEN_LE: "<=",
        TOKEN_LG: "<>",
        TOKEN_LT: "<",
        TOKEN_NE: "!=",
        TOKEN_OR: "||",
        TOKEN_RE: "=~",
    }

    COMPARISON_OPERATORS = frozenset(
        [
            "==",
            ">=",
            ">",
            "<=",
            "<",
            "!=",
            "=~",
        ]
    )

    # Infix operators that accept filter expression literals.
    INFIX_LITERAL_OPERATORS = frozenset(
        [
            "==",
            ">=",
            ">",
            "<=",
            "<",
            "!=",
            "<>",
            "=~",
            "in",
            "contains",
        ]
    )

    PREFIX_OPERATORS = frozenset(
        [
            TOKEN_NOT,
        ]
    )

    RE_FLAG_MAP = {
        "a": re.A,
        "i": re.I,
        "m": re.M,
        "s": re.S,
    }

    _INVALID_NAME_SELECTOR_CHARS = f"[{''.join(INVALID_NAME_SELECTOR_CHARS)}]"
    RE_INVALID_NAME_SELECTOR = re.compile(
        rf'(?:(?!(?<!\\)"){_INVALID_NAME_SELECTOR_CHARS})'
    )

    def __init__(self, *, env: JSONPathEnvironment) -> None:
        self.env = env

        self.token_map: Dict[str, Callable[[TokenStream], BaseExpression]] = {
            TOKEN_DOUBLE_QUOTE_STRING: self.parse_string_literal,
            TOKEN_PSEUDO_ROOT: self.parse_absolute_query,
            TOKEN_FALSE: self.parse_boolean,
            TOKEN_FILTER_CONTEXT: self.parse_filter_context_path,
            TOKEN_FLOAT: self.parse_float_literal,
            TOKEN_FUNCTION: self.parse_function_extension,
            TOKEN_INT: self.parse_integer_literal,
            TOKEN_KEY: self.parse_current_key,
            TOKEN_LBRACKET: self.parse_list_literal,
            TOKEN_LPAREN: self.parse_grouped_expression,
            TOKEN_MISSING: self.parse_undefined,
            TOKEN_NIL: self.parse_nil,
            TOKEN_NONE: self.parse_nil,
            TOKEN_NOT: self.parse_prefix_expression,
            TOKEN_NULL: self.parse_nil,
            TOKEN_RE_PATTERN: self.parse_regex,
            TOKEN_ROOT: self.parse_absolute_query,
            TOKEN_SELF: self.parse_relative_query,
            TOKEN_SINGLE_QUOTE_STRING: self.parse_string_literal,
            TOKEN_TRUE: self.parse_boolean,
            TOKEN_UNDEFINED: self.parse_undefined,
        }

        self.list_item_map: Dict[str, Callable[[TokenStream], BaseExpression]] = {
            TOKEN_FALSE: self.parse_boolean,
            TOKEN_FLOAT: self.parse_float_literal,
            TOKEN_INT: self.parse_integer_literal,
            TOKEN_NIL: self.parse_nil,
            TOKEN_NONE: self.parse_nil,
            TOKEN_NULL: self.parse_nil,
            TOKEN_DOUBLE_QUOTE_STRING: self.parse_string_literal,
            TOKEN_SINGLE_QUOTE_STRING: self.parse_string_literal,
            TOKEN_TRUE: self.parse_boolean,
        }

        self.function_argument_map: Dict[
            str, Callable[[TokenStream], BaseExpression]
        ] = {
            TOKEN_DOUBLE_QUOTE_STRING: self.parse_string_literal,
            TOKEN_PSEUDO_ROOT: self.parse_absolute_query,
            TOKEN_FALSE: self.parse_boolean,
            TOKEN_FILTER_CONTEXT: self.parse_filter_context_path,
            TOKEN_FLOAT: self.parse_float_literal,
            TOKEN_FUNCTION: self.parse_function_extension,
            TOKEN_INT: self.parse_integer_literal,
            TOKEN_KEY: self.parse_current_key,
            TOKEN_NIL: self.parse_nil,
            TOKEN_NONE: self.parse_nil,
            TOKEN_NULL: self.parse_nil,
            TOKEN_ROOT: self.parse_absolute_query,
            TOKEN_SELF: self.parse_relative_query,
            TOKEN_SINGLE_QUOTE_STRING: self.parse_string_literal,
            TOKEN_TRUE: self.parse_boolean,
        }

    def parse(self, stream: TokenStream) -> Iterator[JSONPathSegment]:
        """Parse a JSONPath query from a stream of tokens."""
        # Leading whitespace is not allowed in strict mode.
        if stream.skip_whitespace() and self.env.strict:
            raise JSONPathSyntaxError(
                "unexpected leading whitespace", token=stream.current()
            )

        # Trailing whitespace is not allowed in strict mode.
        if (
            self.env.strict
            and stream.tokens
            and stream.tokens[-1].kind == TOKEN_WHITESPACE
        ):
            raise JSONPathSyntaxError(
                "unexpected trailing whitespace", token=stream.tokens[-1]
            )

        token = stream.current()

        if token.kind == TOKEN_ROOT or (
            token.kind == TOKEN_PSEUDO_ROOT and not self.env.strict
        ):
            stream.next()
        elif self.env.strict:
            # Raises a syntax error because the current token is not TOKEN_ROOT.
            stream.expect(TOKEN_ROOT)

        try:
            yield from self.parse_query(stream)
        except RecursionError as err:
            raise JSONPathRecursionError(str(err), token=None) from err

        if stream.current().kind not in (TOKEN_EOF, TOKEN_INTERSECTION, TOKEN_UNION):
            raise JSONPathSyntaxError(
                f"unexpected token {stream.current().value!r}",
                token=stream.current(),
            )

    def parse_query(self, stream: TokenStream) -> Iterable[JSONPathSegment]:
        """Parse a JSONPath query string.

        This method assumes the root, current or pseudo root identifier has
        already been consumed.
        """
        if not self.env.strict and stream.current().kind in {
            TOKEN_NAME,
            TOKEN_WILD,
            TOKEN_KEYS,
            TOKEN_KEY_NAME,
        }:
            # A non-standard "bare" path. One that starts with a shorthand selector
            # without a leading identifier (`$`, `@`, `^` or `_`).
            #
            # When no identifier is given, a root query (`$`) is assumed.
            token = stream.current()
            selector = self.parse_shorthand_selector(stream)
            yield JSONPathChildSegment(env=self.env, token=token, selectors=(selector,))

        while True:
            stream.skip_whitespace()
            token = stream.next()

            if token.kind == TOKEN_DOT:
                selector = self.parse_shorthand_selector(stream)
                yield JSONPathChildSegment(
                    env=self.env, token=token, selectors=(selector,)
                )
            elif token.kind == TOKEN_DDOT:
                if stream.current().kind == TOKEN_LBRACKET:
                    selectors = tuple(self.parse_bracketed_selection(stream))
                else:
                    selectors = (self.parse_shorthand_selector(stream),)

                yield JSONPathRecursiveDescentSegment(
                    env=self.env, token=token, selectors=selectors
                )
            elif token.kind == TOKEN_LBRACKET:
                stream.pos -= 1
                yield JSONPathChildSegment(
                    env=self.env,
                    token=token,
                    selectors=tuple(self.parse_bracketed_selection(stream)),
                )
            elif token.kind == TOKEN_EOF:
                break
            else:
                # An embedded query. Put the token back on the stream.
                stream.pos -= 1
                break

    def parse_shorthand_selector(self, stream: TokenStream) -> JSONPathSelector:
        token = stream.next()

        if token.kind == TOKEN_NAME:
            return NameSelector(
                env=self.env,
                token=token,
                name=token.value,
            )

        if token.kind == TOKEN_KEY_NAME:
            return KeySelector(
                env=self.env,
                token=token,
                key=token.value,
            )

        if token.kind == TOKEN_WILD:
            return WildcardSelector(
                env=self.env,
                token=token,
            )

        if token.kind == TOKEN_KEYS:
            if stream.current().kind == TOKEN_NAME:
                return KeySelector(
                    env=self.env,
                    token=token,
                    key=self._decode_string_literal(stream.next()),
                )

            return KeysSelector(
                env=self.env,
                token=token,
            )

        raise JSONPathSyntaxError("expected a shorthand selector", token=token)

    def parse_bracketed_selection(self, stream: TokenStream) -> List[JSONPathSelector]:  # noqa: PLR0912, PLR0915
        segment_token = stream.eat(TOKEN_LBRACKET)
        selectors: List[JSONPathSelector] = []

        while True:
            stream.skip_whitespace()
            token = stream.current()

            if token.kind == TOKEN_RBRACKET:
                break

            if token.kind == TOKEN_INT:
                if (
                    stream.peek().kind == TOKEN_COLON
                    or stream.peek(2).kind == TOKEN_COLON
                ):
                    selectors.append(self.parse_slice(stream))
                else:
                    self._raise_for_leading_zero(token)
                    selectors.append(
                        IndexSelector(
                            env=self.env,
                            token=token,
                            index=int(token.value),
                        )
                    )
                    stream.next()
            elif token.kind in (
                TOKEN_DOUBLE_QUOTE_STRING,
                TOKEN_SINGLE_QUOTE_STRING,
            ):
                selectors.append(
                    NameSelector(
                        env=self.env,
                        token=token,
                        name=self._decode_string_literal(token),
                    ),
                )
                stream.next()
            elif token.kind == TOKEN_COLON:
                selectors.append(self.parse_slice(stream))
            elif token.kind == TOKEN_WILD:
                selectors.append(WildcardSelector(env=self.env, token=token))
                stream.next()
            elif token.kind == TOKEN_KEYS:
                stream.eat(TOKEN_KEYS)
                if stream.current().kind in (
                    TOKEN_DOUBLE_QUOTE_STRING,
                    TOKEN_SINGLE_QUOTE_STRING,
                ):
                    selectors.append(
                        KeySelector(
                            env=self.env,
                            token=token,
                            key=self._decode_string_literal(stream.next()),
                        )
                    )
                else:
                    selectors.append(KeysSelector(env=self.env, token=token))

            elif token.kind == TOKEN_FILTER:
                selectors.append(self.parse_filter_selector(stream))
            elif token.kind == TOKEN_KEYS_FILTER:
                selectors.append(self.parse_filter_selector(stream, keys=True))
            elif token.kind in (TOKEN_ROOT, TOKEN_NAME):
                selectors.append(self.parse_singular_query_selector(stream))
            elif token.kind == TOKEN_EOF:
                raise JSONPathSyntaxError("unexpected end of query", token=token)
            else:
                raise JSONPathSyntaxError(
                    f"unexpected token in bracketed selection {token.kind!r}",
                    token=token,
                )

            stream.skip_whitespace()

            if stream.current().kind == TOKEN_EOF:
                raise JSONPathSyntaxError(
                    "unexpected end of segment",
                    token=stream.current(),
                )

            if stream.current().kind != TOKEN_RBRACKET:
                stream.eat(TOKEN_COMMA)
                stream.skip_whitespace()
                if stream.current().kind == TOKEN_RBRACKET:
                    raise JSONPathSyntaxError(
                        "unexpected trailing comma", token=stream.current()
                    )

        stream.eat(TOKEN_RBRACKET)

        if not selectors:
            raise JSONPathSyntaxError("empty bracketed segment", token=segment_token)

        return selectors

    def parse_slice(self, stream: TokenStream) -> SliceSelector:
        """Parse a slice JSONPath expression from a stream of tokens."""
        token = stream.current()
        start: Optional[int] = None
        stop: Optional[int] = None
        step: Optional[int] = None

        def _maybe_index(token: Token) -> bool:
            if token.kind == TOKEN_INT:
                if len(token.value) > 1 and token.value.startswith(("0", "-0")):
                    raise JSONPathSyntaxError(
                        f"invalid index {token.value!r}", token=token
                    )
                return True
            return False

        # 1: or :
        if _maybe_index(stream.current()):
            start = int(stream.current().value)
            stream.next()

        stream.skip_whitespace()
        stream.expect(TOKEN_COLON)
        stream.next()
        stream.skip_whitespace()

        # 1 or 1: or : or ?
        if _maybe_index(stream.current()):
            stop = int(stream.current().value)
            stream.next()
            stream.skip_whitespace()
            if stream.current().kind == TOKEN_COLON:
                stream.next()
        elif stream.current().kind == TOKEN_COLON:
            stream.expect(TOKEN_COLON)
            stream.next()

        # 1 or ?
        stream.skip_whitespace()
        if _maybe_index(stream.current()):
            step = int(stream.current().value)
            stream.next()

        return SliceSelector(
            env=self.env,
            token=token,
            start=start,
            stop=stop,
            step=step,
        )

    def parse_filter_selector(
        self, stream: TokenStream, *, keys: bool = False
    ) -> Union[Filter, KeysFilter]:
        token = stream.next()
        expr = self.parse_filter_expression(stream)

        if self.env.well_typed and isinstance(expr, FunctionExtension):
            func = self.env.function_extensions.get(expr.name)
            if (
                func
                and isinstance(func, FilterFunction)
                and func.return_type == ExpressionType.VALUE
            ):
                raise JSONPathTypeError(
                    f"result of {expr.name}() must be compared", token=token
                )

        if isinstance(expr, (FilterExpressionLiteral, Nil)):
            raise JSONPathSyntaxError(
                "filter expression literals outside of "
                "function expressions must be compared",
                token=token,
            )

        if keys:
            return KeysFilter(
                env=self.env, token=token, expression=FilterExpression(expr)
            )

        return Filter(env=self.env, token=token, expression=FilterExpression(expr))

    def parse_boolean(self, stream: TokenStream) -> BaseExpression:
        if stream.next().kind == TOKEN_TRUE:
            return TRUE
        return FALSE

    def parse_nil(self, stream: TokenStream) -> BaseExpression:
        stream.next()
        return NIL

    def parse_undefined(self, stream: TokenStream) -> BaseExpression:
        stream.next()
        return UNDEFINED_LITERAL

    def parse_string_literal(self, stream: TokenStream) -> BaseExpression:
        return StringLiteral(value=self._decode_string_literal(stream.next()))

    def parse_integer_literal(self, stream: TokenStream) -> BaseExpression:
        token = stream.next()
        value = token.value

        if self.env.strict and value.startswith("0") and len(value) > 1:
            raise JSONPathSyntaxError("invalid integer literal", token=token)

        # Convert to float first to handle scientific notation.
        return IntegerLiteral(value=int(float(value)))

    def parse_float_literal(self, stream: TokenStream) -> BaseExpression:
        token = stream.next()
        value = token.value

        if value.startswith("0") and len(value.split(".")[0]) > 1:
            raise JSONPathSyntaxError("invalid float literal", token=token)

        return FloatLiteral(value=float(value))

    def parse_prefix_expression(self, stream: TokenStream) -> BaseExpression:
        token = stream.next()
        assert token.kind == TOKEN_NOT
        return PrefixExpression(
            operator="!",
            right=self.parse_filter_expression(
                stream, precedence=self.PRECEDENCE_PREFIX
            ),
        )

    def parse_infix_expression(
        self, stream: TokenStream, left: BaseExpression
    ) -> BaseExpression:
        token = stream.next()
        precedence = self.PRECEDENCES.get(token.kind, self.PRECEDENCE_LOWEST)
        right = self.parse_filter_expression(stream, precedence)
        operator = self.BINARY_OPERATORS[token.kind]

        if self.env.well_typed and operator in self.COMPARISON_OPERATORS:
            self._raise_for_non_comparable_function(left, token)
            self._raise_for_non_comparable_function(right, token)

        if operator not in self.INFIX_LITERAL_OPERATORS:
            if isinstance(left, (FilterExpressionLiteral, Nil)):
                raise JSONPathSyntaxError(
                    "filter expression literals outside of "
                    "function expressions must be compared",
                    token=token,
                )
            if isinstance(right, (FilterExpressionLiteral, Nil)):
                raise JSONPathSyntaxError(
                    "filter expression literals outside of "
                    "function expressions must be compared",
                    token=token,
                )

        return InfixExpression(left, operator, right)

    def parse_grouped_expression(self, stream: TokenStream) -> BaseExpression:
        _token = stream.eat(TOKEN_LPAREN)
        expr = self.parse_filter_expression(stream)

        while stream.current().kind != TOKEN_RPAREN:
            token = stream.current()
            if token.kind in (TOKEN_EOF, TOKEN_RBRACKET):
                raise JSONPathSyntaxError("unbalanced parentheses", token=_token)

            expr = self.parse_infix_expression(stream, expr)

        stream.eat(TOKEN_RPAREN)
        return expr

    def parse_absolute_query(self, stream: TokenStream) -> BaseExpression:
        root = stream.next()  # Could be TOKEN_ROOT or TOKEN_PSEUDO_ROOT
        return RootFilterQuery(
            JSONPath(
                env=self.env,
                segments=self.parse_query(stream),
                pseudo_root=root.kind == TOKEN_PSEUDO_ROOT,
            )
        )

    def parse_relative_query(self, stream: TokenStream) -> BaseExpression:
        stream.eat(TOKEN_SELF)
        return RelativeFilterQuery(
            JSONPath(env=self.env, segments=self.parse_query(stream))
        )

    def parse_singular_query_selector(
        self, stream: TokenStream
    ) -> SingularQuerySelector:
        token = (
            stream.next() if stream.current().kind == TOKEN_ROOT else stream.current()
        )

        query = JSONPath(env=self.env, segments=self.parse_query(stream))

        if not query.singular_query():
            raise JSONPathSyntaxError(
                "embedded query selectors must be singular queries", token=token
            )

        return SingularQuerySelector(
            env=self.env,
            token=token,
            query=query,
        )

    def parse_current_key(self, stream: TokenStream) -> BaseExpression:
        stream.next()
        return CURRENT_KEY

    def parse_filter_context_path(self, stream: TokenStream) -> BaseExpression:
        stream.next()
        return FilterContextPath(
            JSONPath(env=self.env, segments=self.parse_query(stream))
        )

    def parse_regex(self, stream: TokenStream) -> BaseExpression:
        pattern = stream.current().value
        flags = 0
        if stream.peek().kind == TOKEN_RE_FLAGS:
            stream.next()
            for flag in set(stream.next().value):
                flags |= self.RE_FLAG_MAP[flag]
        return RegexLiteral(value=re.compile(pattern, flags))

    def parse_list_literal(self, stream: TokenStream) -> BaseExpression:
        stream.eat(TOKEN_LBRACKET)
        list_items: List[BaseExpression] = []

        while True:
            stream.skip_whitespace()

            if stream.current().kind == TOKEN_RBRACKET:
                break

            try:
                list_items.append(self.list_item_map[stream.current().kind](stream))
            except KeyError as err:
                raise JSONPathSyntaxError(
                    f"unexpected {stream.current().value!r}",
                    token=stream.current(),
                ) from err

            stream.skip_whitespace()
            if stream.current().kind != TOKEN_RBRACKET:
                stream.eat(TOKEN_COMMA)
                stream.skip_whitespace()

        stream.eat(TOKEN_RBRACKET)
        return ListLiteral(list_items)

    def parse_function_extension(self, stream: TokenStream) -> BaseExpression:
        function_arguments: List[BaseExpression] = []
        function_token = stream.next()
        stream.eat(TOKEN_LPAREN)

        while True:
            stream.skip_whitespace()
            token = stream.current()

            if token.kind == TOKEN_RPAREN:
                break

            try:
                func = self.function_argument_map[token.kind]
            except KeyError as err:
                raise JSONPathSyntaxError(
                    f"unexpected {token.value!r}", token=token
                ) from err

            expr = func(stream)
            stream.skip_whitespace()

            while stream.current().kind in self.BINARY_OPERATORS:
                expr = self.parse_infix_expression(stream, expr)

            function_arguments.append(expr)
            stream.skip_whitespace()

            if stream.current().kind != TOKEN_RPAREN:
                stream.eat(TOKEN_COMMA)

        stream.eat(TOKEN_RPAREN)

        return FunctionExtension(
            function_token.value,
            self.env.validate_function_extension_signature(
                function_token, function_arguments
            ),
        )

    def parse_filter_expression(
        self, stream: TokenStream, precedence: int = PRECEDENCE_LOWEST
    ) -> BaseExpression:
        stream.skip_whitespace()
        token = stream.current()

        try:
            left = self.token_map[token.kind](stream)
        except KeyError as err:
            if token.kind in (TOKEN_EOF, TOKEN_RBRACKET):
                msg = "end of expression"
            else:
                msg = repr(token.value)
            raise JSONPathSyntaxError(f"unexpected {msg}", token=token) from err

        while True:
            stream.skip_whitespace()
            kind = stream.current().kind

            if (
                kind not in self.BINARY_OPERATORS
                or self.PRECEDENCES.get(kind, self.PRECEDENCE_LOWEST) < precedence
            ):
                break

            left = self.parse_infix_expression(stream, left)

        return left

    def _decode_string_literal(self, token: Token) -> str:
        if self.env.strict:
            # For strict compliance with RC 9535, we must unescape string literals
            # ourself. RFC 9535 is more strict than json.loads when it comes to
            # parsing \uXXXX escape sequences.
            return unescape_string(
                token.value,
                token,
                "'" if token.kind == TOKEN_SINGLE_QUOTE_STRING else '"',
            )

        if self.env.unicode_escape:
            if token.kind == TOKEN_SINGLE_QUOTE_STRING:
                value = token.value.replace('"', '\\"').replace("\\'", "'")
            else:
                value = token.value

            try:
                rv = json.loads(f'"{value}"')
                assert isinstance(rv, str)
                return rv
            except json.JSONDecodeError as err:
                message = f"decode error: {str(err).split(':')[1]}"
                raise JSONPathSyntaxError(message, token=token) from None

        return token.value

    def _raise_for_non_comparable_function(
        self, expr: BaseExpression, token: Token
    ) -> None:
        if isinstance(expr, FilterQuery) and not expr.path.singular_query():
            raise JSONPathTypeError("non-singular query is not comparable", token=token)

        if isinstance(expr, FunctionExtension):
            func = self.env.function_extensions.get(expr.name)
            if (
                isinstance(func, FilterFunction)
                and func.return_type != ExpressionType.VALUE
            ):
                raise JSONPathTypeError(
                    f"result of {expr.name}() is not comparable", token
                )

    def _raise_for_leading_zero(self, token: Token) -> None:
        if (
            len(token.value) > 1 and token.value.startswith("0")
        ) or token.value.startswith("-0"):
            raise JSONPathSyntaxError("leading zero in index selector", token=token)
