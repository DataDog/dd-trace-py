"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.utils.paths``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

import sys
from typing import TYPE_CHECKING

import _ddtrace_internal.utils.paths as _impl


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.utils.paths import _CASE_INSENSITIVE as _CASE_INSENSITIVE  # noqa: F401
    from _ddtrace_internal.utils.paths import _SEP as _SEP  # noqa: F401
    from _ddtrace_internal.utils.paths import AnyPurePath as AnyPurePath  # noqa: F401
    from _ddtrace_internal.utils.paths import _starts_with as _starts_with  # noqa: F401
    from _ddtrace_internal.utils.paths import deepest_containing_root as deepest_containing_root  # noqa: F401
    from _ddtrace_internal.utils.paths import is_contained as is_contained  # noqa: F401
    from _ddtrace_internal.utils.paths import relative_parts as relative_parts  # noqa: F401
    from _ddtrace_internal.utils.paths import relative_path as relative_path  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = _impl
