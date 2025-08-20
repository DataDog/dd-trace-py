# Test file for pin-import rule

# These should trigger the rule (errors):
from ddtrace.trace import Pin  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Pin, Span  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Pin, Span, Tracer, tracer, Context  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Span, Pin  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Span, Tracer, tracer, Context, Pin  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Span, Tracer, Pin, tracer, Context  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Pin as PinAlias  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Pin as PinAlias, Span  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Pin as PinAlias, Span, Tracer, tracer, Context  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Span, Pin as PinAlias  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Span, Tracer, tracer, Context, Pin as PinAlias  # ast-grep-ignore: pin-import-deprecated
from ddtrace.trace import Span, Tracer, Pin as PinAlias, tracer, Context  # ast-grep-ignore: pin-import-deprecated
import ddtrace.trace.Pin  # ast-grep-ignore: pin-import-deprecated

# Direct usage should also be caught
ddtrace.trace.Pin.get_from(service)  # ast-grep-ignore: pin-import-deprecated

# trace.Pin usage after importing trace module
from ddtrace import trace

trace.Pin.get_from(service)  # ast-grep-ignore: pin-import-deprecated

# These should NOT trigger the rule (valid):
from ddtrace._trace.pin import Pin
from ddtrace._trace.pin import Pin as PinAlias
from ddtrace.trace import Span  # Only Span, no Pin
import ddtrace.trace

Pin.get_from(service)  # After proper import
