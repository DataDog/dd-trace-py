"""Class-based function extension base."""

import inspect
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import List

if TYPE_CHECKING:
    from jsonpath.env import JSONPathEnvironment
    from jsonpath.token import Token

from ..exceptions import JSONPathTypeError


def validate(
    _: "JSONPathEnvironment",
    func: Callable[..., Any],
    args: List[Any],
    token: "Token",
) -> List[Any]:
    """Generic validation of function extension arguments using introspection.

    RFC 9535 requires us to reject paths that use filter functions with too
    many or too few arguments.
    """
    params = list(inspect.signature(func).parameters.values())

    # Keyword only params are not supported
    if [p for p in params if p.kind in (p.KEYWORD_ONLY, p.VAR_KEYWORD)]:
        raise JSONPathTypeError(
            f"function {token.value!r} requires keyword arguments",
            token=token,
        )

    # Too few args?
    positional_args = [
        p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if len(args) < len(positional_args):
        raise JSONPathTypeError(
            f"{token.value!r}() requires {len(positional_args)} arguments",
            token=token,
        )

    # Does the signature have var args?
    has_var_args = bool([p for p in params if p.kind == p.VAR_POSITIONAL])

    # Too many args?
    if not has_var_args and len(args) > len(positional_args):
        raise JSONPathTypeError(
            f"{token.value!r}() requires at most "
            f"{len(positional_args) + len(positional_args)} arguments",
            token=token,
        )

    return args
