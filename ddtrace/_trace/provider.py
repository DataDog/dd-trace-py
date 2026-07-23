from typing import Union

from ddtrace._trace.context import Context
from ddtrace._trace.span import Span
from ddtrace.internal.native._native import DD_CONTEXTVAR as _DD_CONTEXTVAR
from ddtrace.internal.native._native import BaseContextProvider
from ddtrace.internal.native._native import DefaultContextProvider


ActiveTrace = Union[Span, Context]

__all__ = [
    "ActiveTrace",
    "BaseContextProvider",
    "DefaultContextProvider",
    "_DD_CONTEXTVAR",
]
