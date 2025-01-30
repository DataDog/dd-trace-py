import os

import ddtrace.auto  # noqa:F401


def application():
    pass


if os.getenv("STOP_AFTER_LOAD"):
    import sys

    sys.exit(0)
