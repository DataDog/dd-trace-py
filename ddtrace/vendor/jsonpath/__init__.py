# SPDX-FileCopyrightText: 2023-present James Prior <jamesgr.prior@gmail.com>
#
# SPDX-License-Identifier: MIT
from __future__ import annotations

from typing import TYPE_CHECKING
from typing import AsyncIterable
from typing import Iterable
from typing import List
from typing import Optional
from typing import Union

from ._types import JSON
from ._types import JSONData
from ._types import JSONScalar
from .env import JSONPathEnvironment
from .exceptions import JSONPatchError
from .exceptions import JSONPatchTestFailure
from .exceptions import JSONPathError
from .exceptions import JSONPathIndexError
from .exceptions import JSONPathNameError
from .exceptions import JSONPathSyntaxError
from .exceptions import JSONPathTypeError
from .exceptions import JSONPointerError
from .exceptions import JSONPointerIndexError
from .exceptions import JSONPointerKeyError
from .exceptions import JSONPointerResolutionError
from .exceptions import JSONPointerTypeError
from .exceptions import RelativeJSONPointerError
from .exceptions import RelativeJSONPointerIndexError
from .exceptions import RelativeJSONPointerSyntaxError
from .filter import UNDEFINED
from .fluent_api import Projection
from .fluent_api import Query
from .lex import Lexer
from .match import JSONPathMatch
from .match import NodeList
from .parse import Parser
from .patch import JSONPatch
from .path import CompoundJSONPath
from .path import JSONPath
from .pointer import JSONPointer
from .pointer import RelativeJSONPointer
from .pointer import resolve

if TYPE_CHECKING:
    from .match import FilterContextVars


__all__ = (
    "compile",
    "CompoundJSONPath",
    "findall_async",
    "findall",
    "finditer_async",
    "finditer",
    "JSONPatch",
    "JSONPatchError",
    "JSONPatchTestFailure",
    "JSONPath",
    "JSONPathEnvironment",
    "JSONPathError",
    "JSONPathIndexError",
    "JSONPathMatch",
    "JSONPathNameError",
    "JSONPathSyntaxError",
    "JSONPathTypeError",
    "JSONPointer",
    "JSONPointerError",
    "JSONPointerIndexError",
    "JSONPointerKeyError",
    "JSONPointerResolutionError",
    "JSONPointerTypeError",
    "Lexer",
    "NodeList",
    "match",
    "Parser",
    "Projection",
    "query",
    "Query",
    "RelativeJSONPointer",
    "RelativeJSONPointerError",
    "RelativeJSONPointerIndexError",
    "RelativeJSONPointerSyntaxError",
    "resolve",
    "JSON",
    "JSONData",
    "JSONScalar",
    "UNDEFINED",
)


# For convenience and to delegate to strict or non-strict environments.
DEFAULT_ENV = JSONPathEnvironment()
_STRICT_ENV = JSONPathEnvironment(strict=True)


def compile(path: str, *, strict: bool = False) -> Union[JSONPath, CompoundJSONPath]:  # noqa: A001
    """Prepare a path string ready for repeated matching against different data.

    Arguments:
        path: A JSONPath as a string.
        strict: When `True`, compile the path for strict compliance with RFC 9535.

    Returns:
        A `JSONPath` or `CompoundJSONPath`, ready to match against some data.
            Expect a `CompoundJSONPath` if the path string uses the _union_ or
            _intersection_ operators.

    Raises:
        JSONPathSyntaxError: If _path_ is invalid.
        JSONPathTypeError: If filter functions are given arguments of an
            unacceptable type.
        JSONPathRecursionError: If _path_ has been crafted in such a way as to
            cause our recursive parser to reach Python's recursion limit.
            `JSONPathRecursionError` inherits from both `JSONPathError` and
            `RecursionError`.
    """
    return _STRICT_ENV.compile(path) if strict else DEFAULT_ENV.compile(path)


def findall(
    path: str,
    data: JSONData,
    *,
    filter_context: Optional[FilterContextVars] = None,
    strict: bool = False,
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
        strict: When `True`, compile and evaluate with strict compliance with
            RFC 9535.

    Returns:
        A list of matched objects. If there are no matches, the list will
            be empty.

    Raises:
        JSONPathSyntaxError: If the path is invalid.
        JSONPathTypeError: If a filter expression attempts to use types in
            an incompatible way.
        JSONPathRecursionError: At path resolution time if `max_recursion_depth`
            is reached with the descendent segment. Or at compile time if _path_
            has been crafted to cause our recursive parser to reach Python's
            recursion limit.
    """
    return (
        _STRICT_ENV.findall(path, data, filter_context=filter_context)
        if strict
        else DEFAULT_ENV.findall(path, data, filter_context=filter_context)
    )


async def findall_async(
    path: str,
    data: JSONData,
    *,
    filter_context: Optional[FilterContextVars] = None,
    strict: bool = False,
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
        strict: When `True`, compile and evaluate with strict compliance with
            RFC 9535.

    Returns:
        A list of matched objects. If there are no matches, the list will
            be empty.

    Raises:
        JSONPathSyntaxError: If the path is invalid.
        JSONPathTypeError: If a filter expression attempts to use types in
            an incompatible way.
        JSONPathRecursionError: At path resolution time if `max_recursion_depth`
            is reached with the descendent segment. Or at compile time if _path_
            has been crafted to cause our recursive parser to reach Python's
            recursion limit.
    """
    return (
        await _STRICT_ENV.findall_async(path, data, filter_context=filter_context)
        if strict
        else await DEFAULT_ENV.findall_async(path, data, filter_context=filter_context)
    )


def finditer(
    path: str,
    data: JSONData,
    *,
    filter_context: Optional[FilterContextVars] = None,
    strict: bool = False,
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
        strict: When `True`, compile and evaluate with strict compliance with
            RFC 9535.

    Returns:
        An iterator yielding `JSONPathMatch` objects for each match.

    Raises:
        JSONPathSyntaxError: If the path is invalid.
        JSONPathTypeError: If a filter expression attempts to use types in
            an incompatible way.
        JSONPathRecursionError: At path resolution time if `max_recursion_depth`
            is reached with the descendent segment. Or at compile time if _path_
            has been crafted to cause our recursive parser to reach Python's
            recursion limit.
    """
    return (
        _STRICT_ENV.finditer(path, data, filter_context=filter_context)
        if strict
        else DEFAULT_ENV.finditer(path, data, filter_context=filter_context)
    )


async def finditer_async(
    path: str,
    data: JSONData,
    *,
    filter_context: Optional[FilterContextVars] = None,
    strict: bool = False,
) -> AsyncIterable[JSONPathMatch]:
    """Find all objects in _data_ matching the JSONPath _path_.

    If _data_ is a string or a file-like objects, it will be loaded
    using `json.loads()` and the default `JSONDecoder`.

    Arguments:
        path: The JSONPath as a string.
        data: A JSON document or Python object implementing the `Sequence`
            or `Mapping` interfaces.
        filter_context: Arbitrary data made available to filters using
            the _filter context_ selector.
        strict: When `True`, compile and evaluate with strict compliance with
            RFC 9535.

    Returns:
        A list of matched objects. If there are no matches, the list will
            be empty.

    Raises:
        JSONPathSyntaxError: If the path is invalid.
        JSONPathTypeError: If a filter expression attempts to use types in
            an incompatible way.
        JSONPathRecursionError: At path resolution time if `max_recursion_depth`
            is reached with the descendent segment. Or at compile time if _path_
            has been crafted to cause our recursive parser to reach Python's
            recursion limit.
    """
    return (
        await _STRICT_ENV.finditer_async(path, data, filter_context=filter_context)
        if strict
        else await DEFAULT_ENV.finditer_async(path, data, filter_context=filter_context)
    )


def match(
    path: str,
    data: JSONData,
    *,
    filter_context: Optional[FilterContextVars] = None,
    strict: bool = False,
) -> Union[JSONPathMatch, None]:
    """Return a `JSONPathMatch` instance for the first object found in _data_.

    `None` is returned if there are no matches.

    Arguments:
        path: The JSONPath as a string.
        data: A JSON document or Python object implementing the `Sequence`
            or `Mapping` interfaces.
        filter_context: Arbitrary data made available to filters using
            the _filter context_ selector.
        strict: When `True`, compile and evaluate with strict compliance with
            RFC 9535.

    Returns:
        A `JSONPathMatch` object for the first match, or `None` if there were
            no matches.

    Raises:
        JSONPathSyntaxError: If the path is invalid.
        JSONPathTypeError: If a filter expression attempts to use types in
            an incompatible way.
        JSONPathRecursionError: At path resolution time if `max_recursion_depth`
            is reached with the descendent segment. Or at compile time if _path_
            has been crafted to cause our recursive parser to reach Python's
            recursion limit.
    """
    return (
        _STRICT_ENV.match(path, data, filter_context=filter_context)
        if strict
        else DEFAULT_ENV.match(path, data, filter_context=filter_context)
    )


def query(
    path: str,
    data: JSONData,
    *,
    filter_context: Optional[FilterContextVars] = None,
    strict: bool = False,
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
        strict: When `True`, compile and evaluate with strict compliance with
            RFC 9535.

    Returns:
        A query iterator.

    Raises:
        JSONPathSyntaxError: If the path is invalid.
        JSONPathTypeError: If a filter expression attempts to use types in
            an incompatible way.
        JSONPathRecursionError: At path resolution time if `max_recursion_depth`
            is reached with the descendent segment. Or at compile time if _path_
            has been crafted to cause our recursive parser to reach Python's
            recursion limit.
    """
    return (
        _STRICT_ENV.query(path, data, filter_context=filter_context)
        if strict
        else DEFAULT_ENV.query(path, data, filter_context=filter_context)
    )
