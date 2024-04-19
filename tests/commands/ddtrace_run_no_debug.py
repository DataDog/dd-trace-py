import logging

from ddtrace._trace.tracer import log


if __name__ == "__main__":
    assert not log.isEnabledFor(logging.DEBUG)
    print("Test success")
