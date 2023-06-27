import logging

from ddtrace.tracer_utils import log


if __name__ == "__main__":
    assert log.isEnabledFor(logging.DEBUG)
    print("Test success")
