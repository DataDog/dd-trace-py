"""Bridge to the extracted ``ddtrace-native`` package: this module *is* ``_ddtrace_native._threads``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state (``periodic_threads``) and tests may patch either path. The ``TYPE_CHECKING`` block only
tells static tools what it exports; the stubs ship in the ``ddtrace-native`` wheel.
"""

from importlib import import_module
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_native._threads import PERIODIC_STOP as PERIODIC_STOP  # noqa: F401
    from _ddtrace_native._threads import PeriodicThread as PeriodicThread  # noqa: F401
    from _ddtrace_native._threads import periodic_threads as periodic_threads  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = import_module("_ddtrace_native._threads")
