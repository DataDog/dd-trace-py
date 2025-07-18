# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True

from libc.stdlib cimport malloc, free


cdef class SimpleMovingAverage:
    """
    Simple Moving Average implementation.
    """

    cdef int size
    cdef int index
    cdef int sum_count
    cdef int sum_total
    cdef int* counts
    cdef int* totals

    def __cinit__(self, int size):
        """
        :param size: The size of the window to calculate the moving average.
        :type size: :obj:`int`
        """
        cdef int i

        if size < 1:
            size = 1

        self.index = 0
        self.size = size
        self.sum_count = 0
        self.sum_total = 0

        # Allocate C arrays for maximum performance
        self.counts = <int*>malloc(size * sizeof(int))
        self.totals = <int*>malloc(size * sizeof(int))

        if not self.counts or not self.totals:
            raise MemoryError("Failed to allocate memory for SMA arrays")

        # Initialize arrays to zero
        for i in range(size):
            self.counts[i] = 0
            self.totals[i] = 0

    def __dealloc__(self):
        """Clean up allocated memory"""
        if self.counts:
            free(self.counts)
        if self.totals:
            free(self.totals)

    cdef double _get(self):
        if self.sum_total == 0:
            return 0.0
        return <double>self.sum_count / <double>self.sum_total

    def get(self):
        """
        Get the current moving average value.

        :return: The moving average as a float.
        :rtype: float
        """
        return self._get()

    cdef void _set(self, int count, int total):
        cdef int old_count, old_total

        # Ensure count doesn't exceed total
        if count > total:
            count = total

        # Get old values at current index
        old_count = self.counts[self.index]
        old_total = self.totals[self.index]

        # Update running sums
        self.sum_count += count - old_count
        self.sum_total += total - old_total

        # Store new values
        self.counts[self.index] = count
        self.totals[self.index] = total

        # Advance index with wraparound
        self.index += 1
        if self.index >= self.size:
            self.index = 0

    def set(self, int count, int total):
        """
        Set the value of the next bucket and update the SMA value.

        :param count: The valid quantity of the next bucket.
        :type count: :obj:`int`
        :param total: The total quantity of the next bucket.
        :type total: :obj:`int`
        """
        self._set(count, total)

    @property
    def current_average(self):
        """Get current average (read-only property)"""
        return self.get()
