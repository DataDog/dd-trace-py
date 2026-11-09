"""Celery beat fork scenario driven by tests/contrib/celery/test_fork_lifecycle.py.

``python fork_lifecycle_beat.py <report-path> <schedule-dir>`` starts an embedded beat process.
Beat forks, closes every inherited descriptor through the patched ``celery.platforms.close_open_fds``
and publishes a task. The beat child records its observations to report-path when the first task is
published, then leaves through os._exit. The script prints the parent's observations as JSON on its
last stdout line.

Not collected by pytest (its name does not start with "test").
"""

import json
import os
import re
import sys
import time
import warnings


warnings.simplefilter("ignore", DeprecationWarning)

import ddtrace  # noqa: E402


ddtrace.patch(celery=True)

import celery  # noqa: E402
import celery.beat  # noqa: E402
from celery.signals import after_task_publish  # noqa: E402

from ddtrace.internal import periodic  # noqa: E402
from ddtrace.internal.native_runtime import get_native_runtime  # noqa: E402
from ddtrace.internal.telemetry import telemetry_writer  # noqa: E402
from ddtrace.trace import tracer  # noqa: E402


REPORT_PATH, SCHEDULE_DIR = sys.argv[1], sys.argv[2]
_WORKER_ENTRY = re.compile(r"WorkerEntry \{ id: \d+, restart_on_fork: (true|false), worker: PausableWorker::(\w+)")


class CountingService(periodic.PeriodicService):
    def __init__(self, autorestart):
        super().__init__(interval=0.02, autorestart=autorestart)
        self.runs = 0

    def periodic(self):
        self.runs += 1


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        time.sleep(0.01)
    return predicate()


def restartable_running():
    entries = _WORKER_ENTRY.findall(get_native_runtime().debug())
    restartable = [state for restart, state in entries if restart == "true"]
    return bool(restartable) and all(state == "Running" for state in restartable)


app = celery.Celery("fork_lifecycle", broker="memory://")
app.conf.beat_schedule = {"probe": {"task": "fork_lifecycle.noop", "schedule": 0.05}}
app.conf.beat_schedule_filename = os.path.join(SCHEDULE_DIR, "beat-schedule")


@app.task(name="fork_lifecycle.noop")
def noop():
    pass


restartable = CountingService(autorestart=True)
not_restartable = CountingService(autorestart=False)
restartable.start()
not_restartable.start()
telemetry_writer.enable()
with tracer.trace("parent.setup"):
    pass
assert wait_for(lambda: restartable.runs > 0 and not_restartable.runs > 0)
parent_pid = os.getpid()


@after_task_publish.connect
def observe_in_beat_child(**kwargs):
    if os.getpid() == parent_pid:
        return
    report = {}
    try:
        with tracer.trace("beat.child"):
            pass
        telemetry_writer.periodic(force_flush=True)
        report["functional"] = "ok"
    except BaseException as e:  # noqa: B036 - native code can raise PanicException
        report["functional"] = repr(e)
    report["native_restartable_running"] = restartable_running()
    runs = restartable.runs
    report["periodic_restarted"] = wait_for(lambda: restartable.runs > runs)
    runs = not_restartable.runs
    # Long enough for the 100 ms ThreadRestartTimer window and a few intervals.
    time.sleep(0.5)
    report["not_restartable_stayed_stopped"] = not_restartable.runs == runs
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f)
    os._exit(0)


beat = celery.beat.EmbeddedService(app, max_interval=0.1)
beat.start()
child_seen = wait_for(lambda: os.path.exists(REPORT_PATH), timeout=30)
beat.join(10)
if beat.is_alive():
    beat.terminate()
    beat.join(5)

runs = restartable.runs
parent = {
    "child_reported": child_seen,
    "beat_exitcode": beat.exitcode,
    "periodic_resumed": wait_for(lambda: restartable.runs > runs),
}
try:
    telemetry_writer.periodic(force_flush=True)
    parent["native_flush"] = "ok"
except BaseException as e:  # noqa: B036
    parent["native_flush"] = repr(e)
sys.stdout.write("\n" + json.dumps(parent) + "\n")
sys.stdout.flush()
os._exit(0)
