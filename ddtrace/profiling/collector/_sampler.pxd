import cython

@cython.final
cdef class CaptureSampler:
    cdef readonly double capture_pct
    cdef double _counter
    cpdef bint capture(self)
