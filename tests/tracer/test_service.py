from ddtrace.internal import service


def test_service_status():
    s = service.Service()
    assert s.status == service.ServiceStatus.STOPPED
    s.start()
    assert s.status == service.ServiceStatus.RUNNING
    s.stop()
    assert s.status == service.ServiceStatus.STOPPED
    s.start()
    assert s.status == service.ServiceStatus.RUNNING
    s.stop()
    assert s.status == service.ServiceStatus.STOPPED
