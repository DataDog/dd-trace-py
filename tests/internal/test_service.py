import os

import pytest


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires fork")
@pytest.mark.subprocess(timeout=30)
def test_service_lock_held_by_another_thread_at_fork_does_not_deadlock_child() -> None:
    """A fork while another thread is inside Service.start must not leave the child's lock held.

    The thread holding _service_lock does not exist in the child, so if the lock is
    inherited in the locked state, the next start/stop in the child blocks forever.
    """
    import os
    import signal
    import threading
    import time
    import typing

    from ddtrace.internal import service

    parent_pid = os.getpid()
    lock_held = threading.Event()
    allow_start_to_finish = threading.Event()

    class SlowStartService(service.Service):
        def _start_service(self, *args: typing.Any, **kwargs: typing.Any) -> None:
            if os.getpid() == parent_pid:
                lock_held.set()
                allow_start_to_finish.wait()

        def _stop_service(self, *args: typing.Any, **kwargs: typing.Any) -> None:
            pass

    svc = SlowStartService()
    starter = threading.Thread(target=svc.start, daemon=True)
    starter.start()
    assert lock_held.wait(5)

    pid = os.fork()
    if pid == 0:
        svc.start()
        svc.stop()
        os._exit(0)

    status: typing.Optional[int] = None
    deadline = time.monotonic() + 5
    while status is None:
        waited_pid, wait_status = os.waitpid(pid, os.WNOHANG)
        if waited_pid:
            status = wait_status
        elif time.monotonic() > deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            break
        else:
            time.sleep(0.01)

    allow_start_to_finish.set()
    starter.join(5)
    svc.stop()

    assert status is not None, "child deadlocked on _service_lock"
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
