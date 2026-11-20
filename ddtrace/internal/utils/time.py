"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.utils.time``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. (``import_module``, not ``import x.y as z``: a package attribute can shadow
a submodule, as ``settings.env`` does.) The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

from importlib import import_module
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.utils.time import HourGlass as HourGlass  # noqa: F401
    from _ddtrace_internal.utils.time import StopWatch as StopWatch  # noqa: F401
    from _ddtrace_internal.utils.time import Time as Time  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = import_module("_ddtrace_internal.utils.time")
