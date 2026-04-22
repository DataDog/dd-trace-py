# cython: cdivision=True
# cython: annotation_typing=False

import cython


@cython.final
cdef class CaptureSampler:
    """Determine the events that should be captured based on a sampling percentage.

    This is a Cython extension type for maximum performance in the lock profiler
    hot path, where capture() is called on every lock acquire/release.
    """

    def __cinit__(self, double capture_pct=100.0) -> None:
        if capture_pct < 0 or capture_pct > 100:
            raise ValueError("Capture percentage should be between 0 and 100 included")
        self.capture_pct = capture_pct
        self._counter = 0.0

    def __repr__(self) -> str:
        return f"CaptureSampler(capture_pct={self.capture_pct!r})"

    cpdef bint capture(self):
        self._counter += self.capture_pct
        if self._counter >= 100:
            self._counter -= 100
            return True
        return False
