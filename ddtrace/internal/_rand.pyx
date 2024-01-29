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
------------------------------------------------------------------------------------------ benchmark 'span-id': 3 tests -----------------------------------------------------------------------------------------
Name (time in ns)                    Min                   Max                  Mean             StdDev                Median                 IQR            Outliers  OPS (Kops/s)            Rounds  Iterations
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
test_rand64bits_no_pid           65.6104 (1.0)         95.5892 (1.0)         69.6381 (1.0)       4.3421 (1.0)         68.5000 (1.0)        3.8624 (1.0)          15;7   14,359.9654 (1.0)         149      100000
test_rand64bits_pid_check       100.8797 (1.54)       134.0079 (1.40)       106.2188 (1.53)      7.3056 (1.68)       103.4951 (1.51)       5.0807 (1.32)          7;6    9,414.5268 (0.66)         74      100000
test_randbits_stdlib          1,455.2116 (22.18)    1,845.0975 (19.30)    1,550.1867 (22.26)    94.0154 (21.65)    1,514.6971 (22.11)    103.4141 (26.77)        11;3      645.0836 (0.04)         61       10000
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

Python 3.7:
------------------------------------------------------------------------------------- benchmark 'span-id': 3 tests ------------------------------------------------------------------------------------
Name (time in ns)                  Min                 Max                Mean             StdDev              Median               IQR            Outliers  OPS (Mops/s)            Rounds  Iterations
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
test_rand64bits_no_pid         59.9690 (1.0)       99.0107 (1.0)       65.1900 (1.0)       5.5437 (1.0)       63.6649 (1.0)      5.0215 (1.0)         17;13       15.3398 (1.0)         147      100000
test_randbits_stdlib          114.1084 (1.90)     169.3871 (1.71)     125.7419 (1.93)     10.6180 (1.92)     122.7273 (1.93)     5.3290 (1.06)         16;9        7.9528 (0.52)         90      100000
test_rand64bits_pid_check     121.8156 (2.03)     168.9837 (1.71)     130.3854 (2.00)      8.5097 (1.54)     127.8620 (2.01)     7.8514 (1.56)          9;5        7.6696 (0.50)         81      100000
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
"""  # noqa: E501
import random

from libc.time cimport time

from ddtrace.internal import forksafe


cdef extern from "_stdint.h" nogil:
    ctypedef unsigned long long uint64_t

cdef uint64_t state


cpdef _getstate():
    return state


cpdef seed():
    global state
    random.seed()
    state = <uint64_t>random.getrandbits(64) ^ <uint64_t>4101842887655102017


# We have to reseed the RNG or we will get collisions between the processes as
# they will share the seed and generate the same random numbers.
forksafe.register(seed)


cpdef rand64bits():
    global state
    state ^= state >> 21
    state ^= state << 35
    state ^= state >> 4
    return <uint64_t>(state * <uint64_t>2685821657736338717)


cpdef rand128bits():
    # Returns a 128bit integer with the following format -> <32-bit unix seconds><32 bits of zero><64 random bits>
    return int(time(NULL)) << 96 | rand64bits()


seed()
