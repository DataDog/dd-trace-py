import os
import sys

from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
from ddtrace.internal.service import ServiceStatus


RuntimeWorker.enable()
assert RuntimeWorker._instance is not None
assert RuntimeWorker._instance.status == ServiceStatus.RUNNING


child_pid = os.fork()
if child_pid == 0:
    assert RuntimeWorker._instance is not None
    assert RuntimeWorker._instance.status == ServiceStatus.RUNNING
else:
    pid, status = os.waitpid(child_pid, 0)
    assert RuntimeWorker._instance is not None
    assert RuntimeWorker._instance.status == ServiceStatus.RUNNING
    sys.exit(os.WEXITSTATUS(status))
