from libc.math cimport log, exp, sqrt, floor
from libc.stdint cimport uint64_t

# SplitMix64 constants
cdef uint64_t SPLITMIX_INC = 0x9E3779B97F4A7C15ULL
cdef uint64_t SPLITMIX_MUL1 = 0xBF58476D1CE4E5B9ULL
cdef uint64_t SPLITMIX_MUL2 = 0x94D049BB133111EBULL
cdef double INV_2_53 = 1.0 / 9007199254740992.0
cdef double TWO_PI = 6.283185307179586

# Module-level RNG state (seeded randomly at import)
cdef uint64_t _rng_state

import os as _os
_rng_state = int.from_bytes(_os.urandom(8), "little")


cdef inline uint64_t _splitmix64() noexcept nogil:
    global _rng_state
    cdef uint64_t z
    _rng_state = _rng_state + SPLITMIX_INC
    z = _rng_state
    z = (z ^ (z >> 30)) * SPLITMIX_MUL1
    z = (z ^ (z >> 27)) * SPLITMIX_MUL2
    return z ^ (z >> 31)


cdef inline double _uniform() noexcept nogil:
    cdef uint64_t x = _splitmix64() >> 11
    return (<double>x + 0.5) * INV_2_53


cdef inline int _poisson_knuth(double lam) noexcept nogil:
    # O(λ) - fast for small λ
    cdef double L = exp(-lam)
    cdef int k = 0
    cdef double p = 1.0
    while p > L:
        k = k + 1
        p = p * _uniform()
    return k - 1 if k > 0 else 0


cdef inline int _poisson_ptrs(double lam) noexcept nogil:
    # O(1) - PTRS transformed-rejection for large λ
    cdef double slam = sqrt(lam)
    cdef double loglam = log(lam)
    cdef double b = 0.931 + 2.53 * slam
    cdef double a = -0.059 + 0.02483 * b
    cdef double inv_alpha = 1.1239 + 1.1328 / (b - 3.4)
    cdef double vr = 0.9277 - 3.6224 / (b - 2.0)
    cdef double U, V, us, k, v, x, rhs
    cdef int ik

    while True:
        U = _uniform() - 0.5
        V = _uniform()
        if U < 0:
            us = 0.5 + U
        else:
            us = 0.5 - U
        k = floor((2.0 * a / us + b) * U + lam + 0.43)
        if us >= 0.07 and V <= vr:
            ik = <int>k
            if ik >= 0:
                return ik
        if k < 0:
            continue
        v = log(V * inv_alpha / (a / (us * us) + b))
        x = k + 1.0
        rhs = -lam + k * loglam - (k * log(x) - k + 0.5 * log(TWO_PI * x))
        if v <= rhs:
            return <int>k


cpdef void seed(uint64_t s):
    global _rng_state
    _rng_state = s


cpdef int sample(double lam):
    if lam <= 0.0:
        return 0
    elif lam < 30.0:
        return _poisson_knuth(lam)
    else:
        return _poisson_ptrs(lam)