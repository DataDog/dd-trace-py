"""Regression test for GH-19526 (issue A): a forked worker must report runtime metrics
under its own runtime-id, not the parent's.

With runtime metrics enabled before fork (gunicorn/uWSGI preload, SSI), every worker
inherits the parent's RuntimeWorker instance. The runtime-id tag used to be snapshotted at
construction, so all N+1 processes reported on a single series and the pod's
runtime.python.mem.rss read parent-sized.
"""

import os
import sys
from unittest import mock

from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker


def emitted_runtime_ids(worker):
    if worker._dogstatsd_client.socket is not None:
        worker._dogstatsd_client.socket.send.reset_mock()
    worker.flush()
    sent = [c.args[0].decode("utf-8") for c in worker._dogstatsd_client.socket.send.mock_calls]
    gauges = [line for packet in sent for line in packet.split("\n") if line]
    assert gauges, "expected at least one metric line to be sent"
    return set(
        tag.split(":", 1)[1]
        for gauge in gauges
        for tag in gauge.partition("|#")[2].split(",")
        if tag.startswith("runtime-id:")
    )


with mock.patch("socket.socket") as sock:
    sock.return_value.getsockopt.return_value = 0

    # Parent: runtime metrics enabled before the fork.
    worker = RuntimeWorker()
    parent_runtime_id = get_runtime_id()
    assert emitted_runtime_ids(worker) == {parent_runtime_id}

    child_pid = os.fork()
    if child_pid == 0:
        # Child: a worker that never re-enables runtime metrics, as under gunicorn.
        child_runtime_id = get_runtime_id()
        assert child_runtime_id != parent_runtime_id, "fork should have issued a new runtime-id"
        assert emitted_runtime_ids(worker) == {child_runtime_id}
        os._exit(0)

    _, status = os.waitpid(child_pid, 0)
    # The parent keeps reporting under its own runtime-id; summing per-runtime-id across the
    # 1 + N series is what makes the total match the sum of per-process RSS.
    assert emitted_runtime_ids(worker) == {parent_runtime_id}
    sys.exit(os.WEXITSTATUS(status))
