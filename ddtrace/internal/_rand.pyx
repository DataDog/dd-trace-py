"""Generator for pseudorandom 64-bit integers.

Implements the xorshift* algorithm with a non-linear transformation
(multiplication) applied to the result.

This implementation uses the recommended constants from Numerical Recipes
Chapter 7 (Ranq1 algorithm).

According to TPV, the period is approx. 1.8 x 10^19. So it should not be used
by an application that makes more than 10^12 calls.

To put this into perspective: we cap the max number of traces at 1k/s let's be
conservative and say each trace contains 100 spans.

That's 100k spans/second which would be 100k + 1 calls to this fn per second.

That's 10,000,000 seconds until we hit the period. That's 115 days of
100k spans/second (with no application restart) until the period is reached.


rand64bits() is thread-safe as it is written in C and is interfaced with via
a single Python step. This is the same mechanism in which CPython achieves
thread-safety:
https://github.com/python/cpython/blob/8d21aa21f2cbc6d50aab3f420bb23be1d081dac4/Lib/random.py#L37-L38


Python 2.7:
Name (time in ns)                         Min                   Max                  Mean              StdDev                Median                 IQR            Outliers  OPS (Kops/s)            Rounds  Iterations
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
rand64bits                           144.1789 (1.01)       221.2596 (1.0)        155.5800 (1.0)       15.4198 (1.0)        151.3100 (1.00)       7.6687 (1.0)           4;6    6,427.5628 (1.0)          61      100000
random.SystemRandom().getrandbits  1,626.8015 (11.37)    2,178.9074 (9.85)     1,766.1762 (11.35)    133.8990 (8.68)     1,714.4561 (11.35)    113.9164 (14.85)        11;8      566.1949 (0.09)         60       10000
-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------


Python 3.7:
Name (time in ns)                       Min                 Max                Mean             StdDev              Median                IQR            Outliers  OPS (Mops/s)            Rounds  Iterations
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
rand64bits                         167.5956 (1.0)      211.3155 (1.0)      190.2803 (1.0)       9.5815 (1.0)      187.7187 (1.0)      11.4513 (1.0)          15;1        5.2554 (1.0)          52      100000
random.randbits                    222.7103 (1.33)     367.4459 (1.74)     250.2699 (1.32)     26.5930 (2.78)     242.1607 (1.29)     26.4550 (2.31)          6;1        3.9957 (0.76)         36      100000
-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
"""
from libc.stdint cimport uint64_t

from ddtrace import compat


cdef uint64_t x = compat.getrandbits(64) ^ 4101842887655102017


def rand64bits():
    global x
    x ^= x >> 21
    x ^= x << 35
    x ^= x >> 4
    return x * <uint64_t>2685821657736338717
