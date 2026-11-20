"""Bridge to the extracted ``ddtrace-internal`` package: this module *is* ``_ddtrace_internal.utils.cache``.

At runtime the module object is replaced by the package module, so there is a single copy of its
state and tests may patch either path. The ``TYPE_CHECKING`` block only tells static tools what it exports.
"""

import sys
from typing import TYPE_CHECKING

import _ddtrace_internal.utils.cache as _impl


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any

    from _ddtrace_internal.utils.cache import WK as WK  # noqa: F401
    from _ddtrace_internal.utils.cache import WV as WV  # noqa: F401
    from _ddtrace_internal.utils.cache import CachedMethodDescriptor as CachedMethodDescriptor  # noqa: F401
    from _ddtrace_internal.utils.cache import F as F  # noqa: F401
    from _ddtrace_internal.utils.cache import IdentityWeakKeyDictionary as IdentityWeakKeyDictionary  # noqa: F401
    from _ddtrace_internal.utils.cache import M as M  # noqa: F401
    from _ddtrace_internal.utils.cache import T as T  # noqa: F401
    from _ddtrace_internal.utils.cache import cached as cached  # noqa: F401
    from _ddtrace_internal.utils.cache import cachedmethod as cachedmethod  # noqa: F401
    from _ddtrace_internal.utils.cache import callonce as callonce  # noqa: F401
    from _ddtrace_internal.utils.cache import is_not_void_function as is_not_void_function  # noqa: F401
    from _ddtrace_internal.utils.cache import miss as miss  # noqa: F401

    def __getattr__(name: str) -> Any: ...

else:
    sys.modules[__name__] = _impl
