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
        # type: (int) -> None
        """
        Constructor for SimpleMovingAverage.

        :param size: The size of the window to calculate the moving average.
        :type size: :obj:`int`
        """
        if size < 1:
            raise ValueError

        self.index = 0
        self.size = size

        self.sum_count = 0
        self.sum_total = 0

        self.counts = [0] * self.size
        self.totals = [0] * self.size

    def get(self):
        # type: () -> float
        """
        Get the current SMA value.
        """
        if self.sum_total == 0:
            return 0.0

        return float(self.sum_count) / self.sum_total

    def set(self, count, total):
        # type: (int, int) -> None
        """
        Set the value of the next bucket and update the SMA value.

        :param count: The valid quantity of the next bucket.
        :type count: :obj:`int`
        :param total: The total quantity of the next bucket.
        :type total: :obj:`int`
        """
        if count > total:
            raise ValueError

        self.sum_count += count - self.counts[self.index]
        self.sum_total += total - self.totals[self.index]

        self.counts[self.index] = count
        self.totals[self.index] = total

        self.index = (self.index + 1) % self.size
