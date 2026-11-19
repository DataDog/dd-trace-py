import pytest

from ddtrace.internal import service


class MyService(service.Service):
    def _start_service(self):
        pass

    def _stop_service(self):
        pass


def test_service_status():
    s = MyService()
    assert s.status == service.ServiceStatus.STOPPED
    s.start()
    assert s.status == service.ServiceStatus.RUNNING
    s.stop()
    assert s.status == service.ServiceStatus.STOPPED
    s.start()
    assert s.status == service.ServiceStatus.RUNNING
    s.stop()
    assert s.status == service.ServiceStatus.STOPPED


def test_service_status_multi_start():
    s = MyService()
    assert s.status == service.ServiceStatus.STOPPED
    s.start()
    with pytest.raises(service.ServiceStatusError) as e:
        s.start()
    assert e.value.current_status == service.ServiceStatus.RUNNING
    assert str(e.value) == "MyService is already in status running"
    assert s.status == service.ServiceStatus.RUNNING
    s.stop()
    with pytest.raises(service.ServiceStatusError) as e:
        s.stop()
    assert e.value.current_status == service.ServiceStatus.STOPPED
    assert str(e.value) == "MyService is already in status stopped"
    assert s.status == service.ServiceStatus.STOPPED


def test_service_status_on_fail():
    class ServiceFail(MyService):
        def _start_service(self):
            raise RuntimeError

    s = ServiceFail()
    with pytest.raises(RuntimeError):
        s.start()
    assert s.status == service.ServiceStatus.STOPPED


def test_service_status_on_fail_stop():
    class ServiceFail(MyService):
        def _stop_service(self):
            raise RuntimeError

    s = ServiceFail()
    s.start()
    with pytest.raises(RuntimeError):
        s.stop()
    assert s.status == service.ServiceStatus.RUNNING
