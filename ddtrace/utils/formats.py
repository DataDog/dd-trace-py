from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..internal.utils.formats import T  # noqa
from ..internal.utils.formats import asbool  # noqa
from ..internal.utils.formats import deep_getattr  # noqa
from ..internal.utils.formats import get_env  # noqa
from ..internal.utils.formats import log  # noqa
from ..internal.utils.formats import parse_tags_str  # noqa
from ..vendor.debtcollector.removals import removed_module


removed_module(
    module="ddtrace.utils.formats",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
