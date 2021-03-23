import subprocess
import sys

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


def test_service_copy():
    s = MyService()
    # Comparison is done by identity because attr.s(eq=False)
    assert s.copy() != s.copy()
    assert s.copy() is not s.copy()
    s.start()
    s2 = s.copy()
    assert s is not s2
    assert s != s2
    assert s.status == service.ServiceStatus.RUNNING
    assert s2.status == service.ServiceStatus.STOPPED
    s.stop()
    assert s is not s2
    assert s != s2
    assert s.status == service.ServiceStatus.STOPPED
    assert s2.status == service.ServiceStatus.STOPPED


def test_forksafe_service():
    class MyForksafeService(service.ForksafeService):
        SERVICE_CLASS = MyService

    sm = MyForksafeService()
    sm.start()
    assert sm.status == sm._service.status == service.ServiceStatus.RUNNING
    with pytest.raises(RuntimeError):
        sm.start()

    sm.stop()
    assert sm.status == sm._service.status == service.ServiceStatus.STOPPED
    with pytest.raises(RuntimeError):
        sm.stop()


def call_program(*args):
    subp = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    stdout, stderr = subp.communicate()
    return stdout, stderr, subp.wait(), subp.pid


def test_forksafe_service_pass_args(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
from ddtrace.internal import service

class MyService(service.Service):
    def _start_service(self, foobar):
        assert foobar == 42
        print("Started")

    def _stop_service(self):
        print("I was stopped, thank you")

class MyForksafeService(service.ForksafeService):
    SERVICE_CLASS = MyService

sm = MyForksafeService()
sm.start(foobar=42)
assert sm.status == service.ServiceStatus.RUNNING
"""
    )
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    assert out == b"Started\nI was stopped, thank you\n"
    assert err == b""


def test_forksafe_service_stop_on_exit(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
from ddtrace.internal import service

class MyService(service.Service):
    def _start_service(self):
        pass

    def _stop_service(self):
        print("I was stopped, thank you")

class MyForksafeService(service.ForksafeService):
    SERVICE_CLASS = MyService

sm = MyForksafeService()
sm.start()
"""
    )
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    assert out == b"I was stopped, thank you\n"
    assert err == b""


def test_forksafe_service_stop_on_exit_false(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
from ddtrace.internal import service

class MyService(service.Service):
    def _start_service(self):
        pass

    def _stop_service(self):
        print("I was stopped, thank you")

class MyForksafeService(service.ForksafeService):
    SERVICE_CLASS = MyService

sm = MyForksafeService()
sm.start(stop_on_exit=False)
"""
    )
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    assert out == b""
    assert err == b""


def test_forksafe_service_start_in_children_false(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
import os
import sys

from ddtrace.internal import service

class MyService(service.Service):
    def _start_service(self):
        pass

    def _stop_service(self):
        print("I was stopped, thank you")

class MyForksafeService(service.ForksafeService):
    SERVICE_CLASS = MyService

sm = MyForksafeService()
sm.start(start_in_children=False)

parent_service_instance = sm._service

child_pid = os.fork()
if child_pid == 0:
    assert parent_service_instance.status == service.ServiceStatus.STOPPED
    assert sm._service == parent_service_instance
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.STOPPED
else:
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
"""
    )
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    # Stopped 2 times:
    # parent stopped in child (at exit())
    # parent stopped in parent (at exit())
    assert out == b"I was stopped, thank you\nI was stopped, thank you\n"
    assert err == b""


def test_forksafe_service_start_in_children_but_stopped(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
import os
import sys

from ddtrace.internal import service

class MyService(service.Service):
    def _start_service(self):
        pass

    def _stop_service(self):
        print("I was stopped, thank you")

class MyForksafeService(service.ForksafeService):
    SERVICE_CLASS = MyService

sm = MyForksafeService()
sm.start(start_in_children=True)

sm.stop()

parent_service_instance = sm._service

child_pid = os.fork()
if child_pid == 0:
    assert parent_service_instance.status == service.ServiceStatus.STOPPED
    assert sm._service == parent_service_instance
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.STOPPED
else:
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.STOPPED
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
"""
    )
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0, (out, err)
    # Stopped 2 times:
    # parent stopped in child (at exit())
    # parent stopped in parent (at exit())
    assert out == b"I was stopped, thank you\nI was stopped, thank you\n"
    assert err == b""


def test_forksafe_service_start_in_children(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
import os
import sys

from ddtrace.internal import service

class MyService(service.Service):
    def _start_service(self):
        pass

    def _stop_service(self):
        print("I was stopped, thank you")

class MyForksafeService(service.ForksafeService):
    SERVICE_CLASS = MyService

sm = MyForksafeService()
sm.start()

parent_service_instance = sm._service

child_pid = os.fork()
if child_pid == 0:
    assert parent_service_instance.status == service.ServiceStatus.STOPPED
    assert sm._service is not parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
else:
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
"""
    )
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    # Stopped 3 times:
    # parent stopped in the child (at fork())
    # child stopped in child (at exit())
    # parent stopped in parent (at exit())
    assert out == b"I was stopped, thank you\nI was stopped, thank you\nI was stopped, thank you\n"
    assert err == b""


def test_forksafe_service_start_in_children_unique_attr(tmpdir):
    pyfile = tmpdir.join("test.py")
    pyfile.write(
        """
import os
import sys
import uuid

import attr

from ddtrace.internal import service

@attr.s
class MyService(service.Service):
    unique = attr.ib(factory=uuid.uuid4)

    def copy(self):
        c = super(MyService, self).copy()
        c.unique = uuid.uuid4()
        return c

    def _start_service(self):
        pass

    def _stop_service(self):
        print("I was stopped, thank you")

class MyForksafeService(service.ForksafeService):
    SERVICE_CLASS = MyService

sm = MyForksafeService()
sm.start()

parent_service_instance = sm._service

child_pid = os.fork()
if child_pid == 0:
    assert parent_service_instance.status == service.ServiceStatus.STOPPED
    assert parent_service_instance.unique != sm._service.unique
    assert sm._service != parent_service_instance
    assert sm._service is not parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
else:
    assert sm._service is parent_service_instance
    assert sm.status == service.ServiceStatus.RUNNING
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
"""
    )
    out, err, status, pid = call_program(sys.executable, str(pyfile))
    assert status == 0
    # Stopped 3 times:
    # parent stopped in the child (at fork())
    # child stopped in child (at exit())
    # parent stopped in parent (at exit())
    assert out == b"I was stopped, thank you\nI was stopped, thank you\nI was stopped, thank you\n"
    assert err == b""
