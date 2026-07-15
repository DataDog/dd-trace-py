import faulthandler
from importlib.metadata import version
import sys
import threading

from ddtrace.internal import periodic
from ddtrace.internal import threads as thread_hooks


faulthandler.enable()

TIMEOUT_SECONDS = 1.0

print("ddtrace=%s" % version("ddtrace"), flush=True)
print("starting PeriodicThread with a blocked callback", flush=True)

callback_started = threading.Event()
callback_continue = threading.Event()
before_fork_done = threading.Event()


def blocked_periodic_callback():
    callback_started.set()
    callback_continue.wait()


worker = periodic.PeriodicThread(
    interval=60.0,
    target=blocked_periodic_callback,
    name="repro:BeforeForkBlockedCallback",
    no_wait_at_start=True,
)
worker.start()

if not callback_started.wait(timeout=TIMEOUT_SECONDS):
    print("setup failed: periodic callback did not start", flush=True)
    sys.exit(2)

print("calling ddtrace.internal.threads._before_fork()", flush=True)
before_fork_thread = threading.Thread(target=lambda: (thread_hooks._before_fork(), before_fork_done.set()))
before_fork_thread.daemon = True
before_fork_thread.start()

try:
    if before_fork_done.wait(timeout=TIMEOUT_SECONDS):
        print("safe: _before_fork returned without waiting for the blocked callback", flush=True)
        exit_code = 0
    else:
        print(
            "REPRODUCED: _before_fork is still blocked after %.1f seconds" % TIMEOUT_SECONDS,
            flush=True,
        )
        faulthandler.dump_traceback(file=sys.stdout)
        exit_code = 1
finally:
    callback_continue.set()
    before_fork_thread.join(timeout=2.0)
    thread_hooks._after_fork_parent()
    worker.stop()
    worker.join(timeout=2.0)

sys.exit(exit_code)
