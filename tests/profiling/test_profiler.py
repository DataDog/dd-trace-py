from ddtrace.profiling import profiler


def test_status():
    p = profiler.Profiler()
    assert repr(p.status) == "STOPPED"
    p.start()
    assert repr(p.status) == "RUNNING"
    p.stop()
    assert repr(p.status) == "STOPPED"


def test_restart():
    p = profiler.Profiler()
    p.start()
    p.stop()
    p.start()
    p.stop()
