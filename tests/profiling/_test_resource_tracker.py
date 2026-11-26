import multiprocessing
from multiprocessing import resource_tracker
import os


def f() -> None:
    pass


if __name__ == "__main__":
    # Spawning a process starts the resource tracker.
    multiprocessing.set_start_method("spawn", force=True)
    p = multiprocessing.Process(target=f)
    p.start()
    p.join(120)

    tracker_pid = resource_tracker._resource_tracker._pid
    # Wait for the tracker to exit, so any profile it flushes at exit is written before the test checks.
    resource_tracker._resource_tracker._stop()

    print(os.getpid())
    print(tracker_pid)
