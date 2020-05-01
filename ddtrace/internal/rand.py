from ddtrace import compat

try:
    from ._rand import rand64bits
except ImportError:

    def rand64bits():
        return compat.getrandbits(64)
