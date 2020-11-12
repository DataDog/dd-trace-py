from ddtrace.internal.sma import SimpleMovingAverage

def test_min_size():
    sma = SimpleMovingAverage(0)

    assert 1 == sma.size
    assert 1 == len(sma.buckets)

def test_moving_average():
    sma = SimpleMovingAverage(4)

    assert 0 == sma.get()
    sma.set(1)
    assert 0.25 == sma.get()
    sma.set(0)
    assert 0.25 == sma.get()
    sma.set(1)
    assert 0.5 == sma.get()
    sma.set(0)
    assert 0.5 == sma.get()
    sma.set(0)
    assert 0.25 == sma.get()
    sma.set(1)
    assert 0.5 == sma.get()
    sma.set(1)
    assert 0.5 == sma.get()
    sma.set(1)
    assert 0.75 == sma.get()
    sma.set(1)
    assert 1 == sma.get()
    sma.set(1)
    assert 1 == sma.get()
