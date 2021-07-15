from ddtrace.contrib.trace_utils import from_wsgi_header
from ddtrace.contrib.trace_utils import get_wsgi_header
from ddtrace.utils.deprecation import deprecation


__all__ = (
    "from_wsgi_header",
    "get_wsgi_header",
)

deprecation(
    name="ddtrace.propagation.utils",
    message="The propagation.utils module has been moved into ddtrace.contrib.trace_utils",
    version="1.0.0",
)
