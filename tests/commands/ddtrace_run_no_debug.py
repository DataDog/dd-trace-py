import logging

from ddtrace.tracer import log


if __name__ == "__main__":
    assert not log.isEnabledFor(logging.DEBUG)
    print("Test success")
