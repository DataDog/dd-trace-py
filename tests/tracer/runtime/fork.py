import os
import sys

from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker


RuntimeWorker.enable()
assert RuntimeWorker._instance is not None
assert RuntimeWorker._instance.is_alive()


child_pid = os.fork()
if child_pid == 0:
    assert RuntimeWorker._instance is not None
    assert RuntimeWorker._instance.is_alive()
else:
    pid, status = os.waitpid(child_pid, 0)
    sys.exit(os.WEXITSTATUS(status))
