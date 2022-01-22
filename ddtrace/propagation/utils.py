from ..internal.utils.deprecation import deprecation
from ._utils import from_wsgi_header  # noqa
from ._utils import get_wsgi_header  # noqa


deprecation(
    name="ddtrace.propagation.utils",
    message="This module will be removed in v1.0.",
    version="1.0.0",
)
