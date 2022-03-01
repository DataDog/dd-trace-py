from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ..vendor.debtcollector.removals import removed_module
from ._utils import from_wsgi_header  # noqa
from ._utils import get_wsgi_header  # noqa


removed_module(
    module="ddtrace.propagation.utils",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
