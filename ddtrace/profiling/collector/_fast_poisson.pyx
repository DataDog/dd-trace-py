from libc.math cimport log, exp, sqrt, floor
from libc.stdint cimport uint64_t

cdef uint64_t SPLITMIX_INC = 0x9E3779B97F4A7C15ULL
cdef uint64_t SPLITMIX_MUL1 = 0xBF58476D1CE4E5B9ULL
cdef uint64_t SPLITMIX_MUL2 = 0x94D049BB133111EBULL
cdef double INV_2_53 = 1.0 / 9007199254740992.0
cdef double TWO_PI = 6.283185307179586

import os as _os

# SplitMix64: Taken from https://prng.di.unimi.it/splitmix64.c
cdef inline uint64_t _splitmix64(uint64_t* state):
    cdef uint64_t z
    state[0] = state[0] + SPLITMIX_INC
    z = state[0]
    z = (z ^ (z >> 30)) * SPLITMIX_MUL1
    z = (z ^ (z >> 27)) * SPLITMIX_MUL2
    return z ^ (z >> 31)

# Convert to double in [0, 1)
cdef inline double _uniform(uint64_t* state):
    cdef uint64_t x = _splitmix64(state) >> 11
    return (<double>x + 0.5) * INV_2_53


# Taken from: https://en.wikipedia.org/wiki/Poisson_distribution#Generating_Poisson-distributed_random_variables
cdef inline int _poisson_knuth(double lam, uint64_t* state):
    cdef double L = exp(-lam)
    cdef int k = 0
    cdef double p = 1.0
    while p > L:
        k = k + 1
        p = p * _uniform(state)
    return k - 1 if k > 0 else 0

# Taken from: https://hpaulkeeler.com/simulating-poisson-random-variables-with-large-means-in-c/
cdef inline int _poisson_ptrs(double lam, uint64_t* state):
    cdef double slam = sqrt(lam)
    cdef double loglam = log(lam)
    cdef double b = 0.931 + 2.53 * slam
    cdef double a = -0.059 + 0.02483 * b
    cdef double inv_alpha = 1.1239 + 1.1328 / (b - 3.4)
    cdef double vr = 0.9277 - 3.6224 / (b - 2.0)
    cdef double U, V, us, k, v, x, rhs
    cdef int ik

    while True:
        U = _uniform(state) - 0.5
        V = _uniform(state)
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


cdef class PoissonSampler:
    """Poisson sampler with per-instance RNG state.

    Each instance maintains its own SplitMix64 state, seeded from os.urandom
    at construction. This ensures that separate callers get independent
    sequences, and that state is fresh after fork (when the profiler is
    restarted and a new instance is created).
    """
    cdef uint64_t _state

    def __init__(self):
        self._state = int.from_bytes(_os.urandom(8), "little")

    cpdef void seed(self, uint64_t s):
        self._state = s

    cpdef int sample(self, double lam):
        if lam <= 0.0:
            return 0
        elif lam < 30.0:
            return _poisson_knuth(lam, &self._state)
        else:
            return _poisson_ptrs(lam, &self._state)
