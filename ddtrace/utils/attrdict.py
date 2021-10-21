from ..internal.utils.attrdict import AttrDict  # noqa
from ..internal.utils.deprecation import deprecation


deprecation(
    name="ddtrace.utils.attrdict",
    message="Use `ddtrace.internal.utils.attrdict` module instead",
    version="1.0.0",
)
