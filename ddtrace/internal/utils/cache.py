"""Legacy re-export shim for a moved module.

``ddtrace.internal.utils.cache`` moved to:

``ddtrace_base.utils.cache``

Keep the historical import path working for external consumers; import from
the new module instead.
"""

from ddtrace_base.utils.cache import WK as WK  # noqa: F401
from ddtrace_base.utils.cache import WV as WV  # noqa: F401
from ddtrace_base.utils.cache import CachedMethodDescriptor as CachedMethodDescriptor  # noqa: F401
from ddtrace_base.utils.cache import F as F  # noqa: F401
from ddtrace_base.utils.cache import IdentityWeakKeyDictionary as IdentityWeakKeyDictionary  # noqa: F401
from ddtrace_base.utils.cache import M as M  # noqa: F401
from ddtrace_base.utils.cache import T as T  # noqa: F401
from ddtrace_base.utils.cache import cached as cached  # noqa: F401
from ddtrace_base.utils.cache import cachedmethod as cachedmethod  # noqa: F401
from ddtrace_base.utils.cache import callonce as callonce  # noqa: F401
from ddtrace_base.utils.cache import is_not_void_function as is_not_void_function  # noqa: F401
from ddtrace_base.utils.cache import miss as miss  # noqa: F401
