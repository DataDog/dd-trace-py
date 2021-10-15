from .internal.encoding import JSONEncoder
from .internal.encoding import JSONEncoderV2
from .internal.encoding import MsgpackEncoderV03 as Encoder
from .utils.deprecation import deprecation


__all__ = (
    "Encoder",
    "JSONEncoder",
    "JSONEncoderV2",
)


deprecation(
    name="ddtrace.encoding",
    message="The encoding module has been moved to ddtrace.internal and will no longer be part of the public API.",
    version="1.0.0",
)
