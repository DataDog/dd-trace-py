try:
    from time import monotonic
except ImportError:
    from time import time as monotonic  # noqa: F401

try:
    from time import monotonic_ns
except ImportError:
    from time import time as _mtime

    def monotonic_ns():
        return int(_mtime() * 10e5) * 1000


try:
    from time import time_ns
except ImportError:
    from time import time as _time

    def time_ns():
        return int(_time() * 10e5) * 1000


try:
    from time import process_time_ns
except ImportError:
    from time import clock as _process_time

    def process_time_ns():
        return int(_process_time() * 10e8)
