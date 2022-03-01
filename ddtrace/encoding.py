from ddtrace.internal.utils.deprecation import DDTraceDeprecationWarning

from .internal.encoding import JSONEncoder
from .internal.encoding import JSONEncoderV2
from .internal.encoding import MsgpackEncoderV03 as MsgpackEncoder
from .vendor.debtcollector.removals import removed_module


Encoder = MsgpackEncoder


__all__ = (
    "Encoder",
    "JSONEncoder",
    "JSONEncoderV2",
    "MsgpackEncoder",
)


removed_module(
    module="ddtrace.encoding",
    category=DDTraceDeprecationWarning,
    removal_version="1.0.0",
)
