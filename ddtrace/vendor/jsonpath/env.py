"""Core JSONPath configuration object."""

from __future__ import annotations

try:
    import regex  # type: ignore # noqa: F401

    REGEX_AVAILABLE = True
except ImportError:
    REGEX_AVAILABLE = False  # type: ignore

try:
    import iregexp_check  # type: ignore # noqa: F401

    IREGEXP_AVAILABLE = True
except ImportError:
    IREGEXP_AVAILABLE = False  # type: ignore

from decimal import Decimal
from operator import getitem
from typing import TYPE_CHECKING
from typing import Any
from typing import AsyncIterable
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Type
from typing import Union

from . import function_extensions
from .exceptions import JSONPathNameError
from .exceptions import JSONPathSyntaxError
from .exceptions import JSONPathTypeError
from .filter import UNDEFINED
from .filter import VALUE_TYPE_EXPRESSIONS
from .filter import BaseExpression
from .filter import FilterQuery
from .filter import FunctionExtension
from .filter import InfixExpression
from .fluent_api import Query
from .function_extensions import ExpressionType
from .function_extensions import FilterFunction
from .function_extensions import validate
from .lex import Lexer
from .match import JSONPathMatch
from .match import NodeList
from .parse import Parser
from .path import CompoundJSONPath
from .path import JSONPath
from .stream import TokenStream
from .token import TOKEN_EOF
from .token import TOKEN_INTERSECTION
from .token import TOKEN_PSEUDO_ROOT
from .token import TOKEN_UNION
from .token import Token

if TYPE_CHECKING:
    from ._types import JSONData
    from .match import FilterContextVars


class JSONPathEnvironment:
    """JSONPath configuration.

    This class contains settings for path tokenization, parsing and resolution
    behavior, plus convenience methods for matching an unparsed path to some
    data.

    Most applications will want to create a single `JSONPathEnvironment`, or
    use `jsonpath.compile()`, `jsonpath.findall()`, etc. from the package-level
    default environment.

    ## Environment customization

    Environment customization is achieved by subclassing `JSONPathEnvironment`
    and overriding class attributes and/or methods. Some of these
    customizations include:

    - Changing the root (`$`), self (`@`) or filter context (`_`) token with
      class attributes `root_token`, `self_token` and `filter_context_token`.
    - Registering a custom lexer or parser with the class attributes
      `lexer_class` or `parser_class`. `lexer_class` must be a subclass of
      [`Lexer`]() and `parser_class` must be a subclass of [`Parser`]().
    - Setup built-in function extensions by overriding
      `setup_function_extensions()`
    - Hook in to mapping and sequence item getting by overriding `getitem()`.
    - Change filter comparison operator behavior by overriding `compare()`.

    Arguments:
        filter_caching (bool): If `True`, filter expressions will be cached
            where possible.
        unicode_escape: If `True`, decode UTF-16 escape sequences found in
            JSONPath string literals.
        well_typed: Control well-typedness checks on filter function expressions.
            If `True` (the default), JSONPath expressions are checked for
            well-typedness as compile time.

            **New in version 0.10.0**
        strict: When `True`, follow RFC 9535 strictly.
            **New in version 2.0.0**

    ## Class attributes

    Attributes:
        pseudo_root_token (str): The pattern used to select a "fake" root node, one
            level above the real root node.
        filter_context_token (str): The pattern used to select extra filter context
            data. Defaults to `"_"`.
        intersection_token (str): The pattern used as the intersection operator.
            Defaults to `"&"`.
        key_token (str): The pattern used to identify the current key or index when
            filtering a mapping or sequence. Defaults to `"#"`.
        keys_selector_token (str): The pattern used as the "keys" selector. Defaults to
            `"~"`.
        keys_filter_token (str): The pattern used as the "keys filter" selector.
            Defaults to `"~?"`.
        lexer_class: The lexer to use when tokenizing path strings.
        max_int_index (int): The maximum integer allowed when selecting array items by
            index. Defaults to `(2**53) - 1`.
        min_int_index (int): The minimum integer allowed when selecting array items by
            index. Defaults to `-(2**53) + 1`.
        max_recursion_depth (int): The maximum number of dict/objects and/or arrays/
            lists the recursive descent selector can visit before a
            `JSONPathRecursionError` is thrown.
        parser_class: The parser to use when parsing tokens from the lexer.
        root_token (str): The pattern used to select the root node in a JSON document.
            Defaults to `"$"`.
        self_token (str): The pattern used to select the current node in a JSON
            document. Defaults to `"@"`
        union_token (str): The pattern used as the union operator. Defaults to `"|"`.
    """

    # These should be unescaped strings. `re.escape` will be called on them
    # automatically when compiling lexer rules.
    pseudo_root_token = "^"
    filter_context_token = "_"
    intersection_token = "&"
    key_token = "#"
    keys_selector_token = "~"
    keys_filter_token = "~?"
    root_token = "$"
    self_token = "@"
    union_token = "|"

    max_int_index = (2**53) - 1
    min_int_index = -(2**53) + 1
    max_recursion_depth = 100

    # Override these to customize path tokenization and parsing.
    lexer_class: Type[Lexer] = Lexer
    parser_class: Type[Parser] = Parser
    match_class: Type[JSONPathMatch] = JSONPathMatch

    def __init__(
        self,
        *,
        filter_caching: bool = True,
        unicode_escape: bool = True,
        well_typed: bool = True,
        strict: bool = False,
    ) -> None:
        self.filter_caching: bool = filter_caching
        """Enable or disable filter expression caching."""

        self.unicode_escape: bool = unicode_escape
        """Enable or disable decoding of UTF-16 escape sequences found in
        JSONPath string literals."""

        self.well_typed: bool = well_typed
        """Control well-typedness checks on filter function expressions."""

        self.strict: bool = strict
        """When `True`, follow RFC 9535 strictly.
        
        This includes things like enforcing a leading root identifier and
        ensuring there's no leading or trailing whitespace when parsing a
        JSONPath query.
        """

        self.regex_available: bool = REGEX_AVAILABLE
        """When `True`, the third party `regex` package is available."""

        self.iregexp_available: bool = IREGEXP_AVAILABLE
        """When `True`, the iregexp_check package is available.
        
        iregexp_check will be used to validate regular expressions against RFC 9485,
        if available.
        """

        self.lexer: Lexer = self.lexer_class(env=self)
        """The lexer bound to this environment."""

        self.parser: Parser = self.parser_class(env=self)
        """The parser bound to this environment."""

        self.function_extensions: Dict[str, Callable[..., Any]] = {}
        """A list of function extensions available to filters."""

        self.setup_function_extensions()

    def compile(self, path: str) -> Union[JSONPath, CompoundJSONPath]:  # noqa: A003
        """Prepare a path string ready for repeated matching against different data.

        Arguments:
            path: A JSONPath as a string.

        Returns:
            A `JSONPath` or `CompoundJSONPath`, ready to match against some data.
                Expect a `CompoundJSONPath` if the path string uses the _union_ or
                _intersection_ operators.

        Raises:
            JSONPathSyntaxError: If _path_ is invalid.
            JSONPathTypeError: If filter functions are given arguments of an
                unacceptable type.
            JSONPathRecursionError: If _path_ has been crafted in such a way as
                to cause our recursive parser to reach Python's recursion limit.
                JSONPathRecursionError inherits from both `JSONPathError` and
                `RecursionError`.
        """
        tokens = self.lexer.tokenize(path)
        stream = TokenStream(tokens)
        pseudo_root = stream.current().kind == TOKEN_PSEUDO_ROOT
        _path: Union[JSONPath, CompoundJSONPath] = JSONPath(
            env=self, segments=self.parser.parse(stream), pseudo_root=pseudo_root
        )

        if stream.skip_whitespace() and self.strict:
            raise JSONPathSyntaxError(
                "unexpected whitespace", token=stream.tokens[stream.pos - 1]
            )

        if stream.current().kind != TOKEN_EOF:
            _path = CompoundJSONPath(env=self, path=_path)
            while stream.current().kind != TOKEN_EOF:
                if stream.peek().kind == TOKEN_EOF:
                    # trailing union or intersection
                    raise JSONPathSyntaxError(
                        f"expected a path after {stream.current().value!r}",
                        token=stream.current(),
                    )

                if stream.current().kind == TOKEN_UNION:
                    stream.next()
                    stream.skip_whitespace()
                    pseudo_root = stream.current().kind == TOKEN_PSEUDO_ROOT
                    _path = _path.union(
                        JSONPath(
                            env=self,
                            segments=self.parser.parse(stream),
                            pseudo_root=pseudo_root,
                        )
                    )
                elif stream.current().kind == TOKEN_INTERSECTION:
                    stream.next()
                    stream.skip_whitespace()
                    pseudo_root = stream.current().kind == TOKEN_PSEUDO_ROOT
                    _path = _path.intersection(
                        JSONPath(
                            env=self,
                            segments=self.parser.parse(stream),
                            pseudo_root=pseudo_root,
                        )
                    )
                else:  # pragma: no cover
                    # Parser.parse catches this too
                    raise JSONPathSyntaxError(  # noqa: TRY003
                        f"unexpected token {stream.current().value!r}",
                        token=stream.current(),
                    )

        return _path

    def findall(
        self,
        path: str,
        data: JSONData,
        *,
        filter_context: Optional[FilterContextVars] = None,
    ) -> List[object]:
        """Find all objects in _data_ matching the JSONPath _path_.

        If _data_ is a string or a file-like objects, it will be loaded
        using `json.loads()` and the default `JSONDecoder`.

        Arguments:
            path: The JSONPath as a string.
            data: A JSON document or Python object implementing the `Sequence`
                or `Mapping` interfaces.
            filter_context: Arbitrary data made available to filters using
                the _filter context_ selector.

        Returns:
            A list of matched objects. If there are no matches, the list will
                be empty.

        Raises:
            JSONPathSyntaxError: If the path is invalid.
            JSONPathTypeError: If a filter expression attempts to use types in
                an incompatible way.
            JSONPathRecursionError: At path resolution time if `max_recursion_depth`
                is reached with the descendent segment. Or at compile time if
                _path_ has been crafted to cause our recursive parser to reach
                Python's recursion limit.
        """
        return self.compile(path).findall(data, filter_context=filter_context)

    def finditer(
        self,
        path: str,
        data: JSONData,
        *,
        filter_context: Optional[FilterContextVars] = None,
    ) -> Iterable[JSONPathMatch]:
        """Generate `JSONPathMatch` objects for each match of _path_ in _data_.

        If _data_ is a string or a file-like objects, it will be loaded using
        `json.loads()` and the default `JSONDecoder`.

        Arguments:
            path: The JSONPath as a string.
            data: A JSON document or Python object implementing the `Sequence`
                or `Mapping` interfaces.
            filter_context: Arbitrary data made available to filters using
                the _filter context_ selector.

        Returns:
            An iterator yielding `JSONPathMatch` objects for each match.

        Raises:
            JSONPathSyntaxError: If the path is invalid.
            JSONPathTypeError: If a filter expression attempts to use types in
                an incompatible way.
            JSONPathRecursionError: At path resolution time if `max_recursion_depth`
                is reached with the descendent segment. Or at compile time if
                _path_ has been crafted to cause our recursive parser to reach
                Python's recursion limit.
        """
        return self.compile(path).finditer(data, filter_context=filter_context)

    def match(
        self,
        path: str,
        data: JSONData,
        *,
        filter_context: Optional[FilterContextVars] = None,
    ) -> Union[JSONPathMatch, None]:
        """Return a `JSONPathMatch` instance for the first object found in _data_.

        `None` is returned if there are no matches.

        Arguments:
            path: The JSONPath as a string.
            data: A JSON document or Python object implementing the `Sequence`
                or `Mapping` interfaces.
            filter_context: Arbitrary data made available to filters using
                the _filter context_ selector.

        Returns:
            A `JSONPathMatch` object for the first match, or `None` if there were
                no matches.

        Raises:
            JSONPathSyntaxError: If the path is invalid.
            JSONPathTypeError: If a filter expression attempts to use types in
                an incompatible way.
            JSONPathRecursionError: At path resolution time if `max_recursion_depth`
                is reached with the descendent segment. Or at compile time if
                _path_ has been crafted to cause our recursive parser to reach
                Python's recursion limit.
        """
        return self.compile(path).match(data, filter_context=filter_context)

    def query(
        self,
        path: str,
        data: JSONData,
        *,
        filter_context: Optional[FilterContextVars] = None,
    ) -> Query:
        """Return a `Query` iterator over matches found by applying _path_ to _data_.

        `Query` objects are iterable.

        ```
        for match in jsonpath.query("$.foo..bar", data):
            ...
        ```

        You can skip and limit results with `Query.skip()` and `Query.limit()`.

        ```
        matches = (
            jsonpath.query("$.foo..bar", data)
            .skip(5)
            .limit(10)
        )

        for match in matches
            ...
        ```

        `Query.tail()` will get the last _n_ results.

        ```
        for match in jsonpath.query("$.foo..bar", data).tail(5):
            ...
        ```

        Get values for each match using `Query.values()`.

        ```
        for obj in jsonpath.query("$.foo..bar", data).limit(5).values():
            ...
        ```

        Arguments:
            path: The JSONPath as a string.
            data: A JSON document or Python object implementing the `Sequence`
                or `Mapping` interfaces.
            filter_context: Arbitrary data made available to filters using
                the _filter context_ selector.

        Returns:
            A query iterator.

        Raises:
            JSONPathSyntaxError: If the path is invalid.
            JSONPathTypeError: If a filter expression attempts to use types in
                an incompatible way.
            JSONPathRecursionError: At path resolution time if `max_recursion_depth`
                is reached with the descendent segment. Or at compile time if
                _path_ has been crafted to cause our recursive parser to reach
                Python's recursion limit.
        """
        return Query(self.finditer(path, data, filter_context=filter_context), self)

    async def findall_async(
        self,
        path: str,
        data: JSONData,
        *,
        filter_context: Optional[FilterContextVars] = None,
    ) -> List[object]:
        """An async version of `findall()`."""
        return await self.compile(path).findall_async(
            data, filter_context=filter_context
        )

    async def finditer_async(
        self,
        path: str,
        data: JSONData,
        *,
        filter_context: Optional[FilterContextVars] = None,
    ) -> AsyncIterable[JSONPathMatch]:
        """An async version of `finditer()`."""
        return await self.compile(path).finditer_async(
            data, filter_context=filter_context
        )

    def setup_function_extensions(self) -> None:
        """Initialize function extensions."""
        self.function_extensions["length"] = function_extensions.Length()
        self.function_extensions["count"] = function_extensions.Count()
        self.function_extensions["match"] = function_extensions.Match()
        self.function_extensions["search"] = function_extensions.Search()
        self.function_extensions["value"] = function_extensions.Value()

        if not self.strict:
            self.function_extensions["isinstance"] = function_extensions.IsInstance()
            self.function_extensions["is"] = self.function_extensions["isinstance"]
            self.function_extensions["typeof"] = function_extensions.TypeOf()
            self.function_extensions["type"] = self.function_extensions["typeof"]
            self.function_extensions["startswith"] = function_extensions.StartsWith()

    def validate_function_extension_signature(
        self, token: Token, args: List[Any]
    ) -> List[Any]:
        """Compile-time validation of function extension arguments.

        RFC 9535 requires us to reject paths that use filter functions with
        too many or too few arguments.
        """
        try:
            func = self.function_extensions[token.value]
        except KeyError as err:
            raise JSONPathNameError(
                f"function {token.value!r} is not defined", token=token
            ) from err

        # Type-aware function extensions use the spec's type system.
        if self.well_typed and isinstance(func, FilterFunction):
            self.check_well_typedness(token, func, args)
            return args

        # A callable with a `validate` method?
        if hasattr(func, "validate"):
            args = func.validate(self, args, token)
            assert isinstance(args, list)
            return args

        # Generic validation using introspection.
        return validate(self, func, args, token)

    def check_well_typedness(
        self,
        token: Token,
        func: FilterFunction,
        args: List[BaseExpression],
    ) -> None:
        """Check the well-typedness of a function's arguments at compile-time."""
        # Correct number of arguments?
        if len(args) != len(func.arg_types):
            plural = "" if len(func.arg_types) == 1 else "s"
            raise JSONPathTypeError(
                f"{token.value}() requires {len(func.arg_types)} argument{plural}",
                token=token,
            )

        # Argument types
        for idx, typ in enumerate(func.arg_types):
            arg = args[idx]
            if typ == ExpressionType.VALUE:
                if not (
                    isinstance(arg, VALUE_TYPE_EXPRESSIONS)
                    or (isinstance(arg, FilterQuery) and arg.path.singular_query())
                    or (self._function_return_type(arg) == ExpressionType.VALUE)
                ):
                    raise JSONPathTypeError(
                        f"{token.value}() argument {idx} must be of ValueType",
                        token=token,
                    )
            elif typ == ExpressionType.LOGICAL:
                if not isinstance(arg, (FilterQuery, InfixExpression)):
                    raise JSONPathTypeError(
                        f"{token.value}() argument {idx} must be of LogicalType",
                        token=token,
                    )
            elif typ == ExpressionType.NODES and not (
                isinstance(arg, FilterQuery)
                or self._function_return_type(arg) == ExpressionType.NODES
            ):
                raise JSONPathTypeError(
                    f"{token.value}() argument {idx} must be of NodesType",
                    token=token,
                )

    def _function_return_type(self, expr: BaseExpression) -> Optional[ExpressionType]:
        """Return the type returned from a filter function.

        If _expr_ is not a `FunctionExtension` or the registered function definition is
        not type-aware, return `None`.
        """
        if not isinstance(expr, FunctionExtension):
            return None
        func = self.function_extensions.get(expr.name)
        if isinstance(func, FilterFunction):
            return func.return_type
        return None

    def getitem(self, obj: Any, key: Any) -> Any:
        """Sequence and mapping item getter used throughout JSONPath resolution.

        The default implementation of `getitem` simply calls `operators.getitem()`
        from Python's standard library. Same as `obj[key]`.

        Arguments:
            obj: A mapping or sequence that might contain _key_.
            key: A mapping key, sequence index or sequence slice.
        """
        return getitem(obj, key)

    async def getitem_async(self, obj: Any, key: object) -> Any:
        """An async sequence and mapping item getter."""
        if hasattr(obj, "__getitem_async__"):
            return await obj.__getitem_async__(key)
        return getitem(obj, key)

    def is_truthy(self, obj: object) -> bool:
        """Test for truthiness when evaluating JSONPath filter expressions.

        In some cases, RFC 9535 requires us to test for existence rather than
        truthiness. So the default implementation returns `True` for empty
        collections and `None`. The special `UNDEFINED` object means that
        _obj_ was missing, as opposed to an explicit `None`.

        Arguments:
            obj: Any object.

        Returns:
            `True` if the object exists and is not `False` or `0`.
        """
        if isinstance(obj, NodeList) and len(obj) == 0:
            return False
        if obj is UNDEFINED:
            return False
        if obj is None:
            return True
        return bool(obj)

    def compare(  # noqa: PLR0911
        self, left: object, operator: str, right: object
    ) -> bool:
        """Object comparison within JSONPath filters.

        Override this to customize filter expression comparison operator
        behavior.

        Args:
            left: The left hand side of the comparison expression.
            operator: The comparison expression's operator.
            right: The right hand side of the comparison expression.

        Returns:
            `True` if the comparison between _left_ and _right_, with the
            given _operator_, is truthy. `False` otherwise.
        """
        if operator == "&&":
            return self.is_truthy(left) and self.is_truthy(right)
        if operator == "||":
            return self.is_truthy(left) or self.is_truthy(right)
        if operator == "==":
            return self._eq(left, right)
        if operator == "!=":
            return not self._eq(left, right)
        if operator == "<":
            return self._lt(left, right)
        if operator == ">":
            return self._lt(right, left)
        if operator == ">=":
            return self._lt(right, left) or self._eq(left, right)
        if operator == "<=":
            return self._lt(left, right) or self._eq(left, right)
        if operator == "in" and isinstance(right, (Mapping, Sequence)):
            return left in right
        if operator == "contains" and isinstance(left, (Mapping, Sequence)):
            return right in left
        if operator == "=~" and hasattr(right, "fullmatch") and isinstance(left, str):
            # Right should be a regex.Pattern or an re.Pattern.
            return bool(right.fullmatch(left))
        return False

    def _eq(self, left: object, right: object) -> bool:  # noqa: PLR0911
        if isinstance(right, NodeList):
            left, right = right, left

        if isinstance(left, NodeList):
            if isinstance(right, NodeList):
                return left == right
            if left.empty():
                return right is UNDEFINED
            if len(left) == 1:
                return left[0] == right
            return False

        if left is UNDEFINED and right is UNDEFINED:
            return True

        # Remember 1 == True and 0 == False in Python
        if isinstance(right, bool):
            left, right = right, left

        if isinstance(left, bool):
            return isinstance(right, bool) and left == right

        return left == right

    def _lt(self, left: object, right: object) -> bool:
        if isinstance(left, str) and isinstance(right, str):
            return left < right

        if isinstance(left, (int, float, Decimal)) and isinstance(
            right, (int, float, Decimal)
        ):
            return left < right

        return False
