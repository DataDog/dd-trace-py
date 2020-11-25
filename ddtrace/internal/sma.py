from __future__ import division


class SimpleMovingAverage(object):
    """
    Simple Moving Average implementation.
    """

    __slots__ = (
        "size",
        "index",
        "counts",
        "totals",
        "sum_count",
        "sum_total",
    )

    def __init__(self, size):
        """
        Constructor for SimpleMovingAverage.

        :param size: The size of the window to calculate the moving average.
        :type size: :obj:`int`
        """
        self.index = 0
        self.size = max(1, size)

        self.sum_count = 0
        self.sum_total = 0

        self.counts = [0] * self.size
        self.totals = [0] * self.size

    def get(self):
        """
        Get the current SMA value.
        """
        if self.sum_total == 0:
            return 0.0

        return float(self.sum_count) / self.sum_total

    def set(self, count, total):
        """
        Set the value of the next bucket and update the SMA value.

        :param count: The valid quantity of the next bucket.
        :type count: :obj:`unsigned int`
        :param total: The total quantity of the next bucket.
        :type total: :obj:`unsigned int`
        """
        self.sum_count += count - self.counts[self.index]
        self.sum_total += total - self.totals[self.index]

        self.counts[self.index] = count
        self.totals[self.index] = total

        self.index = (self.index + 1) % self.size
