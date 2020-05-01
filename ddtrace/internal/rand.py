import threading

from ddtrace import compat

if compat.PY3:

    def randbits_gen():
        while True:
            yield compat.getrandbits(64)

    gen = randbits_gen()

    def get_rand64_gen():
        return gen


else:

    try:
        from ._rand import xorshift64s, get_rand64_gen
    except ImportError:

        def xorshift64s():
            """Generator for pseudorandom 64-bit integers.

            See the documentation in _rand.pyx for the description
            of this algorithm.
            """
            x = compat.getrandbits(64) ^ 4101842887655102017

            while True:
                x ^= x >> 21
                x ^= x << 35
                # Python integers are unbounded
                x &= 0xFFFFFFFFFFFFFFFF
                x ^= x >> 4
                yield (x * 2685821657736338717) & 0xFFFFFFFFFFFFFFFF

        loc = threading.local()

        def get_rand64_gen():
            gen = getattr(loc, "rand64bits", None)
            if not gen:
                gen = xorshift64s()
                setattr(loc, "rand64bits", gen)
            return gen
