"""JSONPath exceptions."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Optional

from .token import TOKEN_EOF

if TYPE_CHECKING:
    from .token import Token


class JSONPathError(Exception):
    """Base exception for all errors.

    Arguments:
        args: Arguments passed to `Exception`.
        token: The token that caused the error.
    """

    def __init__(self, *args: object, token: Optional[Token] = None) -> None:
        super().__init__(*args)
        self.token: Optional[Token] = token

    def __str__(self) -> str:
        return self.detailed_message()

    def detailed_message(self) -> str:
        """Return an error message formatted with extra context info."""
        if not self.token:
            return super().__str__()

        lineno, col, _prev, current, _next = self._error_context(
            self.token.path, self.token.index
        )

        if self.token.kind == TOKEN_EOF:
            col = len(current)

        pad = " " * len(str(lineno))
        length = len(self.token.value)
        pointer = (" " * col) + ("^" * max(length, 1))

        return (
            f"{self.message}\n"
            f"{pad} -> {self.token.path!r} {lineno}:{col}\n"
            f"{pad} |\n"
            f"{lineno} | {current}\n"
            f"{pad} | {pointer} {self.message}\n"
        )

    @property
    def message(self) -> object:
        """The exception's error message if one was given."""
        if self.args:
            return self.args[0]
        return None

    def _error_context(self, text: str, index: int) -> tuple[int, int, str, str, str]:
        lines = text.splitlines(keepends=True)
        cumulative_length = 0
        target_line_index = -1

        for i, line in enumerate(lines):
            cumulative_length += len(line)
            if index < cumulative_length:
                target_line_index = i
                break

        if target_line_index == -1:
            raise ValueError("index is out of bounds for the given string")

        # Line number (1-based)
        line_number = target_line_index + 1
        # Column number within the line
        column_number = index - (cumulative_length - len(lines[target_line_index]))

        previous_line = (
            lines[target_line_index - 1].rstrip() if target_line_index > 0 else ""
        )
        current_line = lines[target_line_index].rstrip()
        next_line = (
            lines[target_line_index + 1].rstrip()
            if target_line_index < len(lines) - 1
            else ""
        )

        return line_number, column_number, previous_line, current_line, next_line


class JSONPathSyntaxError(JSONPathError):
    """An exception raised when parsing a JSONPath string.

    Arguments:
        args: Arguments passed to `Exception`.
        token: The token that caused the error.
    """

    def __init__(self, *args: object, token: Token) -> None:
        super().__init__(*args)
        self.token = token


class JSONPathTypeError(JSONPathError):
    """An exception raised due to a type error.

    This should only occur at when evaluating filter expressions.
    """


class JSONPathIndexError(JSONPathError):
    """An exception raised when an array index is out of range.

    Arguments:
        args: Arguments passed to `Exception`.
        token: The token that caused the error.
    """

    def __init__(self, *args: object, token: Token) -> None:
        super().__init__(*args)
        self.token = token


class JSONPathNameError(JSONPathError):
    """An exception raised when an unknown function extension is called.

    Arguments:
        args: Arguments passed to `Exception`.
        token: The token that caused the error.
    """

    def __init__(self, *args: object, token: Token) -> None:
        super().__init__(*args)
        self.token = token


class JSONPathRecursionError(JSONPathError, RecursionError):
    """An exception raised when the maximum recursion depth is reached.

    This could be raised at parse time if a JSONPath query is constructed in
    such a way to hit Python's recursion limit. Or at query resolution time
    if `max_recursion_depth` is reached by the descendant segment (`..`).

    Arguments:
        args: Arguments passed to `Exception`.
        token: The token that caused the error.
    """

    def __init__(self, *args: object, token: Optional[Token]) -> None:
        super().__init__(*args)
        self.token = token


class JSONPointerError(Exception):
    """Base class for all JSON Pointer errors."""


class JSONPointerResolutionError(JSONPointerError):
    """Base exception for those that can be raised during pointer resolution."""


class JSONPointerIndexError(JSONPointerResolutionError, IndexError):
    """An exception raised when an array index is out of range."""

    def __str__(self) -> str:
        return f"pointer index error: {super().__str__()}"


class JSONPointerKeyError(JSONPointerResolutionError, KeyError):
    """An exception raised when a pointer references a mapping with a missing key."""

    def __str__(self) -> str:
        return f"pointer key error: {super().__str__()}"


class JSONPointerTypeError(JSONPointerResolutionError, TypeError):
    """An exception raised when a pointer resolves a string against a sequence."""

    def __str__(self) -> str:
        return f"pointer type error: {super().__str__()}"


class RelativeJSONPointerError(Exception):
    """Base class for all Relative JSON Pointer errors."""


class RelativeJSONPointerIndexError(RelativeJSONPointerError):
    """An exception raised when modifying a pointer index out of range."""


class RelativeJSONPointerSyntaxError(RelativeJSONPointerError):
    """An exception raised when we fail to parse a relative JSON Pointer."""

    def __init__(self, msg: str, rel: str) -> None:
        super().__init__(msg)
        self.rel = rel

    def __str__(self) -> str:
        if not self.rel:
            return super().__str__()

        msg = self.rel[:7]
        if len(msg) == 6:  # noqa: PLR2004
            msg += ".."
        return f"{super().__str__()} {msg!r}"


class JSONPatchError(Exception):
    """Base class for all JSON Patch errors."""


class JSONPatchTestFailure(JSONPatchError):  # noqa: N818
    """An exception raised when a JSON Patch _test_ op fails."""


def _truncate_message(value: str, num: int, end: str = "...") -> str:
    if len(value) < num:
        return value
    return f"{value[: num - len(end)]}{end}"


def _truncate_words(val: str, num: int, end: str = "...") -> str:
    # Replaces consecutive whitespace with a single newline.
    words = val.split()
    if len(words) < num:
        return " ".join(words)
    return " ".join(words[:num]) + end
