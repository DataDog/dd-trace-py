from ..internal.utils.deprecation import deprecation
from ..internal.utils.formats import T  # noqa
from ..internal.utils.formats import asbool  # noqa
from ..internal.utils.formats import deep_getattr  # noqa
from ..internal.utils.formats import get_env  # noqa
from ..internal.utils.formats import parse_tags_str  # noqa


deprecation(
    name="ddtrace.utils.formats",
    message="Use `ddtrace.internal.utils.formats` module instead",
    version="1.0.0",
)
