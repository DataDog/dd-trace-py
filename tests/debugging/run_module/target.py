import threading
from time import sleep

from tests.debugging.mocking import TestDebugger


def foo():
    concealed = "secret"  # noqa
    return


def test():
    sleep(0.5)

    foo()

    (snapshot,) = TestDebugger._instance.test_queue

    assert snapshot.probe.probe_id == "run_module_test"
    assert snapshot.frame.f_locals["concealed"] == "secret"

    print("OK")


threading.Thread(target=test).start()
