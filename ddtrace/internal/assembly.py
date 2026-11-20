"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.assembly``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

import sys
from typing import TYPE_CHECKING

import _ddtrace_internal.assembly as _impl


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.assembly import Assembly as Assembly  # noqa: F401
    from _ddtrace_internal.assembly import BindOpArg as BindOpArg  # noqa: F401
    from _ddtrace_internal.assembly import ParsedLine as ParsedLine  # noqa: F401
    from _ddtrace_internal.assembly import relocate as relocate  # noqa: F401
    from _ddtrace_internal.assembly import transform_instruction as transform_instruction  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = _impl
