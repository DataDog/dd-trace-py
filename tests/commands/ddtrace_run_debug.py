import logging

from ddtrace.trace.internal.tracer import log


if __name__ == "__main__":
    assert log.isEnabledFor(logging.DEBUG)
    print("Test success")
