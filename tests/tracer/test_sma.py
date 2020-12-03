from ddtrace.internal.sma import SimpleMovingAverage


def test_min_size():
    sma = SimpleMovingAverage(0)

    assert 1 == sma.size
    assert 1 == len(sma.counts)
    assert 1 == len(sma.totals)


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
