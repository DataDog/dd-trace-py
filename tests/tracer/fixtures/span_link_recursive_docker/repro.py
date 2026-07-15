import faulthandler
from importlib.metadata import version
import sys

from ddtrace._trace._span_link import SpanLink


faulthandler.enable()

print("ddtrace=%s" % version("ddtrace"), flush=True)
print("creating recursive SpanLink attribute", flush=True)

recursive = []
recursive.append(recursive)
link = SpanLink(trace_id=1, span_id=2, attributes={"recursive": recursive})

print("calling SpanLink.to_dict(); vulnerable builds crash with SIGSEGV", flush=True)
try:
    link.to_dict()
except (RecursionError, ValueError) as exc:
    print("safe rejection: %s: %s" % (type(exc).__name__, exc), flush=True)
    sys.exit(0)
except BaseException as exc:
    print("unexpected Python exception: %s: %s" % (type(exc).__name__, exc), flush=True)
    sys.exit(2)

print("unexpected success: recursive attributes were serialized", flush=True)
sys.exit(3)
