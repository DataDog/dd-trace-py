from ddtrace.profiling import _service


def test_service_status():
    s = _service.Service()
    assert s.status == _service.ServiceStatus.STOPPED
    s.start()
    assert s.status == _service.ServiceStatus.RUNNING
    s.stop()
    assert s.status == _service.ServiceStatus.STOPPED
    s.start()
    assert s.status == _service.ServiceStatus.RUNNING
    s.stop()
    assert s.status == _service.ServiceStatus.STOPPED
