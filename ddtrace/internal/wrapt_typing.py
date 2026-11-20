"""Typing helpers for ``wrapt``."""

from typing import TYPE_CHECKING
from typing import Any

import wrapt


if TYPE_CHECKING:
    # wrapt's type stubs make ObjectProxy generic in the type of the wrapped object, so a bare base class is a
    # `type-arg` error. Subscripting the class raises TypeError at runtime on the wrapt versions we support, hence
    # the alias is only visible to type checkers. Without the stubs, `wrapt` resolves to `Any` and so does this.
    ObjectProxy = wrapt.ObjectProxy[Any]
else:
    ObjectProxy = wrapt.ObjectProxy


__all__ = ["ObjectProxy"]
