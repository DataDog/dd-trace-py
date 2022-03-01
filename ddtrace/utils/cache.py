from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.cache import CachedMethodDescriptor  # noqa
from ..internal.utils.cache import F  # noqa
from ..internal.utils.cache import M  # noqa
from ..internal.utils.cache import S  # noqa
from ..internal.utils.cache import T  # noqa
from ..internal.utils.cache import cached  # noqa
from ..internal.utils.cache import cachedmethod  # noqa
from ..internal.utils.cache import miss  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.cache",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
