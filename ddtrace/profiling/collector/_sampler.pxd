import cython

@cython.final
cdef class CaptureSampler:
    cdef readonly double capture_pct
    cdef readonly double _counter
    cdef bint _bypass
    cpdef bint capture(self)
