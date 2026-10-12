"""F3: hung periodic work (ddup.upload) wedges Profiler.stop() AND process exit.

Two scenarios, each in a child process (a wedged profiler parks native threads,
so the child must os._exit to stay sane):
  --child-stop    : arm hung upload, then stop() under a 5s watchdog.
                    Child exits 42 if stop() did not return -> stop wedged.
  --child-atexit  : arm hung upload, let the scheduler park in it, then return
                    from main *normally*. If interpreter exit (native atexit
                    join of periodic threads) wedges, the child never exits.
Parent runs both children with timeouts and reports. Exit 1 if either wedges.
"""
import os
import subprocess
import sys
import threading
import time

import _common


def make_parked_child(hang_upload=True):
    from ddtrace.internal.datadog.profiling import ddup

    hang = threading.Event()

    def hung_upload(*a, **kw):
        hang.wait()  # park forever

    ddup.upload = hung_upload
    p = _common.make_profiler()
    p.start()
    time.sleep(0.8)  # first flush (interval 0.2s) parks the scheduler worker
    return p


def child_stop():
    p = make_parked_child()
    done = threading.Event()

    def _run():
        try:
            p.stop(flush=True)
        except BaseException as e:  # noqa: BLE001
            print(f"[F3] stop() raised: {e!r}", flush=True)
        done.set()

    threading.Thread(target=_run, daemon=True).start()
    if not done.wait(5.0):
        print("[F3] HUNG: stop() did not return within 5s while ddup.upload hung", flush=True)
        os._exit(42)
    print("[F3] stop() returned despite hung upload", flush=True)
    os._exit(0)


def child_atexit():
    p = make_parked_child()
    print(
        "[F3] main returning normally; scheduler worker is parked in hung upload "
        "- if the process does not exit within ~15s, interpreter exit is wedged",
        flush=True,
    )
    # Deliberately no stop(), no os._exit: exercise the atexit path.


def parent():
    me = sys.executable
    here = os.path.abspath(__file__)
    reproduced = False

    r = subprocess.run([me, here, "--child-stop"], timeout=30)
    if r.returncode == 42:
        print("[F3] REPRODUCED (a): hung ddup.upload wedges Profiler.stop()", flush=True)
        reproduced = True
    else:
        print(f"[F3] (a) stop() not wedged (child exit {r.returncode})", flush=True)

    try:
        r2 = subprocess.run([me, here, "--child-atexit"], timeout=20)
        print(f"[F3] (b) process exit not wedged (child exit {r2.returncode})", flush=True)
    except subprocess.TimeoutExpired:
        print("[F3] REPRODUCED (b): hung periodic work wedges PROCESS EXIT via native atexit join", flush=True)
        reproduced = True

    return 1 if reproduced else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child-stop":
        child_stop()
    elif len(sys.argv) > 1 and sys.argv[1] == "--child-atexit":
        child_atexit()
    else:
        sys.exit(parent())
