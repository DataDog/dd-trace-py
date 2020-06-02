"""Generator for pseudorandom 64-bit integers.

Implements the xorshift* algorithm with a non-linear transformation
(multiplication) applied to the result.

This implementation uses the recommended constants from Numerical Recipes
3rd Edition Chapter 7 (Ranq1 algorithm).

According to TPV, the period is approx. 1.8 x 10^19. So it should not be used
by an application that makes more than 10^12 calls.

To put this into perspective: we cap the max number of traces at 1k/s let's be
conservative and say each trace contains 100 spans.

That's 100k spans/second which would be 100k + 1 calls to this fn per second.

That's 10,000,000 seconds until we hit the period. That's 115 days of
100k spans/second (with no application restart) until the period is reached.


rand64bits() is thread-safe as it is compiled and is interfaced with via a
single Python step. This is the same mechanism in which CPython achieves
thread-safety:
https://github.com/python/cpython/blob/8d21aa21f2cbc6d50aab3f420bb23be1d081dac4/Lib/random.py#L37-L38


Warning: this RNG needs to be reseeded on fork() if collisions are to be
avoided across processes. Reseeding is accomplished simply by calling seed().


Benchmarks (run on 2019 13-inch macbook pro 2.8 GHz quad-core i7)::

    $  pytest --benchmark-enable tests/benchmark.py


Python 2.7:
Name (time in ns)                         Min                   Max                  Mean              StdDev                Median                 IQR            Outliers  OPS (Kops/s)            Rounds  Iterations
------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
rand64bits                             57.7784 (1.0)        350.5182 (1.0)         92.9604 (1.0)       46.1449 (1.0)         74.9111 (1.0)       37.9109 (1.0)          14;9   10,757.2728 (1.0)         134      100000
random.SystemRandom().getrandbits   1,938.8914 (33.56)    3,924.7036 (11.20)    2,555.3982 (27.49)    456.7493 (9.90)     2,373.6000 (31.69)    566.6792 (14.95)        11;1      391.3284 (0.04)         47       10000
------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


Python 3.7:
Name (time in ns)                     Min                 Max                Mean             StdDev              Median                IQR            Outliers  OPS (Mops/s)            Rounds  Iterations
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
rand64bits                        57.5356 (1.0)      151.9838 (1.0)       79.8573 (1.0)      20.5218 (1.0)       73.8342 (1.0)      24.0444 (1.0)         24;10       12.5223 (1.0)         147      100000
random.getrandbits               111.7341 (1.94)     224.2096 (1.48)     137.4291 (1.72)     24.7072 (1.20)     126.5603 (1.71)     24.1269 (1.00)         15;6        7.2765 (0.58)         82      100000
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
"""
from libc.stdint cimport uint64_t
import os

from ddtrace.vendor.wrapt import wrap_function_wrapper
from ddtrace import compat


cdef uint64_t state


cpdef _getstate():
    return state


cpdef seed():
    global state
    state = <uint64_t>compat.getrandbits(64) ^ <uint64_t>4101842887655102017


cpdef rand64bits():
    global state
    state ^= state >> 21
    state ^= state << 35
    state ^= state >> 4
    return <uint64_t>(state * <uint64_t>2685821657736338717)


# Should be available in Python 3.7+
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=seed)


def patch_stdlib_seed():
    """Patches random.seed() to also reseed our RNG.

    This is done because many libraries will call random.seed() after forking
    to reseed the generator for the new process.
    """

    def patched_seed(func, instance, args, kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            # We need the random module to reseed before we do since we use
            # it to seed ourselves.
            seed()

    wrap_function_wrapper("random", "seed", patched_seed)


seed()

patch_stdlib_seed()
