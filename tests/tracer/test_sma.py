from ddtrace.internal.sma import SimpleMovingAverage, DEFAULT_SMA_WINDOW


def test_min_size():
    sma = SimpleMovingAverage()

    assert DEFAULT_SMA_WINDOW == sma.size
    assert DEFAULT_SMA_WINDOW == len(sma.counts)
    assert DEFAULT_SMA_WINDOW == len(sma.totals)

    try:
        sma = SimpleMovingAverage(size=0)
    except Exception as e:
        assert isinstance(e, ValueError)


def test_count_greater_than_total():
    sma = SimpleMovingAverage()

    try:
        sma.set(2, 1)
    except Exception as e:
        assert isinstance(e, ValueError)


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

    sma = SimpleMovingAverage()

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
