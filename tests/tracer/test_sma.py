from ddtrace.internal.sma import SimpleMovingAverage
from ddtrace.internal.writer import DEFAULT_SMA_WINDOW


def test_min_size():
    sma = SimpleMovingAverage(DEFAULT_SMA_WINDOW)

    assert DEFAULT_SMA_WINDOW == sma.size
    assert DEFAULT_SMA_WINDOW == len(sma.counts)
    assert DEFAULT_SMA_WINDOW == len(sma.totals)

    sma = SimpleMovingAverage(0)
    assert sma.size == 1


def test_count_greater_than_total():
    sma = SimpleMovingAverage(DEFAULT_SMA_WINDOW)
    sma.set(2, 1)


def test_moving_average():
    sma = SimpleMovingAverage(4)

    assert 0.0 == sma.get()
    sma.set(1, 2)
    assert 0.5 == sma.get()
    sma.set(2, 2)
    assert 0.75 == sma.get()
    sma.set(1, 4)
    assert 0.5 == sma.get()
    sma.set(0, 12)
    assert 0.2 == sma.get()
    sma.set(2, 2)
    assert 0.25 == sma.get()
    sma.set(15, 18)
    assert 0.5 == sma.get()

    sma = SimpleMovingAverage(1)

    assert 0.0 == sma.get()
    sma.set(1, 2)
    assert 0.5 == sma.get()
    sma.set(2, 2)
    assert 1.0 == sma.get()
    sma.set(0, 0)
    assert 0.0 == sma.get()

    sma = SimpleMovingAverage(DEFAULT_SMA_WINDOW)

    assert 0.0 == sma.get()
    sma.set(1, 1)
    assert 1.0 == sma.get()
    sma.set(0, 0)
    assert 1.0 == sma.get()
    sma.set(0, 0)
    assert 1.0 == sma.get()
    sma.set(0, 4)
    assert 0.2 == sma.get()
    sma.set(1, 3)
    assert 0.25 == sma.get()
    sma.set(1, 4)
    assert 0.25 == sma.get()
    sma.set(0, 0)
    assert 0.25 == sma.get()
    sma.set(0, 0)
    assert 0.25 == sma.get()
    sma.set(0, 0)
    assert 0.25 == sma.get()
    sma.set(7, 8)
    assert 0.5 == sma.get()
    sma.set(1, 1)
    assert 0.5 == sma.get()
    sma.set(10, 20)
    assert 0.5 == sma.get()


class _EmptiedBetweenReads(SimpleMovingAverage):
    """Report a nonzero sum_total on the first read only, as when a concurrent set() empties the window."""

    __slots__ = ("reads",)

    @property
    def sum_total(self):
        self.reads += 1
        return 4 if self.reads == 1 else 0

    @sum_total.setter
    def sum_total(self, value):
        pass


def test_get_does_not_divide_by_a_sum_that_a_concurrent_set_emptied():
    # Writers call get() from span.finish() without the lock that guards set(), so a ZeroDivisionError
    # here raises into application code.
    sma = _EmptiedBetweenReads(4)
    sma.reads = 0
    sma.sum_count = 1
    assert sma.get() == 0.25
