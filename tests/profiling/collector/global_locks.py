import threading

from tests.profiling.collector.lock_utils import init_linenos


init_linenos(__file__)
global_lock = threading.Lock()  # !CREATE! global_lock


def foo():
    global global_lock
    with global_lock:  # !ACQUIRE! !RELEASE! global_lock
        pass


class Bar:
    def __init__(self):
        self.bar_lock = threading.Lock()  # !CREATE! bar_lock

    def bar(self):
        with self.bar_lock:  # !ACQUIRE! !RELEASE! bar_lock
            pass


bar_instance = Bar()
