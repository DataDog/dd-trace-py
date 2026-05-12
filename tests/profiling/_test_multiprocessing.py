import multiprocessing
import os
import sys


def f() -> None:
    import ddtrace.profiling.bootstrap

    profiler = ddtrace.profiling.bootstrap.profiler  # pyright: ignore[reportAttributeAccessIssue]
    # Do some CPU work to ensure we get samples
    total = 0
    for i in range(5_000_000):
        total += i
    # Manually stop the profiler: atexit hooks are not called in subprocesses launched by multiprocessing and we want to
    # be sure the profile are flushed out
    profiler.stop()


if __name__ == "__main__":
    print(os.getpid())
    multiprocessing.set_start_method(sys.argv[1], force=True)

    p = multiprocessing.Process(target=f)
    p.start()
    print(p.pid)
    p.join(120)
