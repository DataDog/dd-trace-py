"""F7: multi-threaded fork() DeprecationWarning breaks subprocess stderr checks.

Symptom: the fork-while-running test's child prints Python 3.12's
DeprecationWarning ('use of fork() may lead to deadlocks in the child') on
stderr, failing the subprocess marker's empty-stderr assertion.
Exit 1 if the warning appears on a profiling child's stderr.
"""
import os
import subprocess
import sys
import threading
import time

import _common


def child():
    p = _common.make_profiler()
    p.start()
    time.sleep(0.3)  # ensure profiler threads exist
    pid = os.fork()
    if pid == 0:
        os._exit(0)  # child of fork: exit immediately, no Python machinery
    os.waitpid(pid, 0)
    os._exit(0)


def main():
    me = sys.executable
    here = os.path.abspath(__file__)
    try:
        r = subprocess.run([me, here, "--child"], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        print("[F7] child wedged (fork in multithreaded process - itself notable)", flush=True)
        return 1
    sys.stderr.flush()
    print(f"[F7] child exit: {r.returncode}", flush=True)
    print(f"[F7] child stderr: {r.stderr.decode(errors='replace')!r}", flush=True)
    if b"DeprecationWarning" in r.stderr:
        print("[F7] REPRODUCED: fork() DeprecationWarning on stderr fails the subprocess stderr assertion", flush=True)
        return 1
    print("[F7] NOT REPRODUCED: no DeprecationWarning on child stderr", flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child()
    else:
        sys.exit(main())
