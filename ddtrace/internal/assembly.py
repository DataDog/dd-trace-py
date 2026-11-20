"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.assembly``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. (``import_module``, not ``import x.y as z``: a package attribute can shadow
a submodule, as ``settings.env`` does.) The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

from importlib import import_module
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.assembly import Assembly as Assembly  # noqa: F401
    from _ddtrace_internal.assembly import BindOpArg as BindOpArg  # noqa: F401
    from _ddtrace_internal.assembly import ParsedLine as ParsedLine  # noqa: F401
    from _ddtrace_internal.assembly import relocate as relocate  # noqa: F401
    from _ddtrace_internal.assembly import transform_instruction as transform_instruction  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = import_module("_ddtrace_internal.assembly")
