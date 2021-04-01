import pytest

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


def test_service_status_on_fail():
    class ServiceFail(service.Service):
        def _start(self):
            raise RuntimeError

    s = ServiceFail()
    with pytest.raises(RuntimeError):
        s.start()
    assert s.status == service.ServiceStatus.STOPPED
