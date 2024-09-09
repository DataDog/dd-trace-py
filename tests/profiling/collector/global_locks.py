import threading


global_lock = threading.Lock()


def foo():
    global global_lock
    with global_lock:
        pass


class Bar:
    def __init__(self):
        self.bar_lock = threading.Lock()

    def bar(self):
        with self.bar_lock:
            pass


bar_instance = Bar()
