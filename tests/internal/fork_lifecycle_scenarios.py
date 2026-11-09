"""Fork lifecycle scenarios driven by tests/internal/test_fork_lifecycle_contract.py.

Run one scenario per interpreter: ``python fork_lifecycle_scenarios.py <scenario>``. The native
fork handlers are process-global, so scenarios must not share a process. The script prints one
JSON report on its last stdout line:

    {"scenario": ..., "parent": {...}, "child": {...}, "intermediate": {...}}

"parent" holds what the forking process observed after the fork returned. "child" holds what the
(innermost) forked child observed. "intermediate" is only set for nested forks and holds what the
middle process observed after its own fork returned. Every boolean is True when the fork
lifecycle contract holds for that observation; strings carry the outcome of native calls.

The script is not collected by pytest (its name does not start with "test").
"""

import ctypes
import errno
import faulthandler
import json
import os
import platform
import re
import select
import signal
import struct
import subprocess
import sys
import threading
import time
import warnings


# Python 3.12+ warns when forking a multi-threaded process; the warning is expected here.
warnings.simplefilter("ignore", DeprecationWarning)

from ddtrace.internal import forksafe  # noqa: E402
from ddtrace.internal import periodic  # noqa: E402
from ddtrace.internal import threads  # noqa: E402
from ddtrace.internal.native_runtime import get_native_runtime  # noqa: E402
from ddtrace.internal.telemetry import telemetry_writer  # noqa: E402
from ddtrace.trace import tracer  # noqa: E402


CHILD_TIMEOUT = 60  # seconds before a child kills itself with SIGALRM
PARENT_DEADLINE = 45.0  # seconds before the parent SIGKILLs a child
RESTART_TIMEOUT = 3.0  # seconds to wait for a periodic service to run again

# PyDLL keeps the GIL held across the call. A CDLL call would release it, so another thread could
# hold the GIL at the time of a raw fork and the child would deadlock on its first bytecode.
_libc = ctypes.PyDLL(None, use_errno=True)
_libc.fork.restype = ctypes.c_int

_WORKER_ENTRY = re.compile(r"WorkerEntry \{ id: (\d+), restart_on_fork: (true|false), worker: PausableWorker::(\w+)")
_NEXT_WORKER_ID = re.compile(r"next_worker_id: (\d+) \}$")


class CountingService(periodic.ForksafeAwakeablePeriodicService):
    def __init__(self, interval=0.05, autorestart=True):
        super().__init__(interval=interval, autorestart=autorestart)
        self.runs = 0
        self.resets = 0

    def periodic(self):
        self.runs += 1

    def reset(self):
        self.resets += 1


def wait_for(predicate, timeout=RESTART_TIMEOUT, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def native_snapshot():
    """Summarize the shared runtime from its Debug representation."""
    text = get_native_runtime().debug()
    entries = [(int(i), restart == "true", state) for i, restart, state in _WORKER_ENTRY.findall(text)]
    next_id = _NEXT_WORKER_ID.search(text)
    return {
        "entries": len(entries),
        "running": sum(1 for _, _, state in entries if state == "Running"),
        "restartable": sorted(i for i, restart, _ in entries if restart),
        "restartable_running": sorted(i for i, restart, state in entries if restart and state == "Running"),
        "next_worker_id": int(next_id.group(1)) if next_id else None,
        "runtime_present": "runtime: Mutex { data: Some(" in text,
    }


def call(fn, timeout=3.0):
    """Run fn in a thread; return "ok", the exception repr, or "hang"."""
    result = ["hang"]

    def target():
        try:
            fn()
            result[0] = "ok"
        except BaseException as e:  # noqa: B036 - native code can raise PanicException
            result[0] = repr(e)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    return result[0]


class Harness:
    """Services shared by every scenario, set up in the root process before any fork."""

    def __init__(self):
        self.svc = CountingService()
        self.svc.start()
        telemetry_writer.enable()
        with tracer.trace("parent.setup"):
            pass
        telemetry_writer.periodic(force_flush=True)
        assert wait_for(lambda: self.svc.runs >= 1), "periodic service never ran"
        self.worker = telemetry_writer._worker
        assert self.worker is not None, "telemetry worker was not built"
        self.before = native_snapshot()
        self.before_fork_hook_ran = False
        self.runs_at_fork = self.resets_at_fork = 0
        forksafe.register_before_fork(self._mark_before_fork)

    def _mark_before_fork(self):
        self.before_fork_hook_ran = True

    def prepare_fork(self):
        # Raw forks skip the Python hooks, so record the counters here as well.
        self.before_fork_hook_ran = False
        self.runs_at_fork = self.svc.runs
        self.resets_at_fork = self.svc.resets

    def child_observe(self, rebuild_first=False):
        """Observe the fork lifecycle contract from a freshly forked child."""
        # Taken before any native call, so it reflects what the fork prepare step left behind.
        pre = native_snapshot()
        report = {
            "prepare_native": pre["running"] == 0,
            # The Python before-fork hooks ran for this fork and stopped the service thread.
            "prepare_periodic": self.before_fork_hook_ran and self.svc._worker in threads._threads_to_restart_after_fork,
        }
        if rebuild_first:
            # Another component reaches the shared runtime before the inherited worker is used.
            report["rebuild_first"] = call(lambda: telemetry_writer.periodic(force_flush=True))
        report["inherited_flush"] = call(self.worker.flush)

        def functional():
            with tracer.trace("child.span"):
                pass
            telemetry_writer.periodic(force_flush=True)

        report["functional"] = call(functional)
        report["worker_preserved"] = telemetry_writer._worker is self.worker
        report["inherited_flush_after_use"] = call(self.worker.flush)

        post = native_snapshot()
        report["native_restartable_running"] = bool(post["restartable"]) and (
            post["restartable_running"] == post["restartable"]
        )
        report["native_restartable_kept"] = set(self.before["restartable"]) <= set(post["restartable"])

        report["periodic_restarted"] = wait_for(lambda: self.svc.runs > self.runs_at_fork)
        report["periodic_reset"] = self.svc.resets > self.resets_at_fork
        return report

    def parent_observe(self):
        """Observe that the forking process resumed both kinds of service."""
        runs = self.svc.runs
        self.svc.awake()
        report = {
            "periodic_resumed": wait_for(lambda: self.svc.runs > runs),
            "native_flush": call(self.worker.flush),
            "worker_preserved": telemetry_writer._worker is self.worker,
            "fork_lock_released": not forksafe._fork_lock._is_owned(),
        }
        after = native_snapshot()
        report["native_counts_match"] = (after["entries"], after["running"]) == (
            self.before["entries"],
            self.before["running"],
        )
        report["native_runtime_preserved"] = after["next_worker_id"] == self.before["next_worker_id"]
        # A fork lock left held by a broken fork would block this from another thread.
        acquired = [False]

        def try_lock():
            acquired[0] = forksafe._fork_lock.acquire(timeout=2)
            if acquired[0]:
                forksafe._fork_lock.release()

        t = threading.Thread(target=try_lock)
        t.start()
        t.join(5)
        report["fork_lock_acquirable"] = acquired[0]
        return report


def raw_fork():
    pid = _libc.fork()
    if pid < 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    return pid


FORKS = {"os": os.fork, "libc": raw_fork}


def run_child(harness, fork_kind, child_body, deadline=PARENT_DEADLINE):
    """Fork with fork_kind, run child_body(harness) in the child and return its JSON result.

    The child reports over a pipe and always leaves through os._exit so no atexit hook runs.
    """
    r, w = os.pipe()
    harness.prepare_fork()
    pid = FORKS[fork_kind]()
    if pid == 0:
        os.close(r)
        signal.alarm(CHILD_TIMEOUT)
        code = 0
        try:
            result = child_body(harness)
        except BaseException as e:  # noqa: B036
            result = {"error": repr(e)}
            code = 3
        try:
            os.write(w, json.dumps(result).encode())
        finally:
            os._exit(code)
    os.close(w)
    data = b""
    hung = False
    deadline = time.monotonic() + deadline
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([r], [], [], remaining)[0]:
            # The pipe stays open while the child lives, so this is a hang.
            hung = True
            # Leave the child's stacks on stderr to tell which fork step blocked.
            os.kill(pid, signal.SIGUSR1)
            time.sleep(0.5)
            os.kill(pid, signal.SIGKILL)
            break
        chunk = os.read(r, 65536)
        if not chunk:
            break
        data += chunk
    os.close(r)
    status = os.waitpid(pid, 0)[1]
    try:
        result = json.loads(data) if data else {"error": "no report"}
    except ValueError:
        result = {"error": "bad report %r" % data[:200]}
    if hung:
        result["error"] = "hang"
    result["exit_ok"] = os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
    return result


def scenario_single(harness, kind, rebuild_first=False):
    child = run_child(harness, kind, lambda h: h.child_observe(rebuild_first=rebuild_first))
    return {"parent": harness.parent_observe(), "child": child}


def scenario_nested(harness, outer, inner, forget_dead_threads=False):
    def middle(h):
        if forget_dead_threads:
            # A raw fork leaves the parent's periodic threads registered although they did not
            # survive, and os.fork would then block joining them. Forget them so the nested
            # native fork path can be observed on its own.
            threads.periodic_threads.clear()
        # The middle process forks again before touching the runtime, the way a pre-fork server
        # forked from a supervisor would.
        grandchild = run_child(h, inner, lambda h2: h2.child_observe(), deadline=PARENT_DEADLINE / 2)
        return {"child": grandchild, "intermediate": h.parent_observe()}

    result = run_child(harness, outer, middle)
    child = result.get("child", {"error": result.get("error", "no report")})
    return {
        "parent": harness.parent_observe(),
        "intermediate": dict(result.get("intermediate", {}), exit_ok=result["exit_ok"]),
        "child": child,
    }


def merge_all(reports):
    """Fold many child reports into one: a check holds only if it held in every child."""
    merged = {}
    for report in reports:
        for key, value in report.items():
            ok = value is True or value == "ok"
            prev = merged.get(key)
            if prev is None or prev is True or prev == "ok":
                merged[key] = value if not ok else (True if isinstance(value, bool) else "ok")
    merged["count"] = len(reports)
    return merged


def scenario_threads(harness, kind, n_threads=4, forks_per_thread=5):
    reports = []
    lock = threading.Lock()

    def light_child(h):
        pre = native_snapshot()
        report = {"prepare_native": pre["running"] == 0}
        if kind == "libc":
            report["inherited_flush"] = call(h.worker.flush)
        with tracer.trace("child.span"):
            pass
        report["functional"] = call(lambda: telemetry_writer.periodic(force_flush=True))
        post = native_snapshot()
        report["native_restartable_running"] = bool(post["restartable"]) and (
            post["restartable_running"] == post["restartable"]
        )
        return report

    def worker():
        for _ in range(forks_per_thread):
            result = run_child(harness, kind, light_child)
            with lock:
                reports.append(result)

    workers = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(PARENT_DEADLINE * forks_per_thread)
    return {"parent": harness.parent_observe(), "child": merge_all(reports)}


def scenario_exec(harness, preexec_fn=False, force_fork=False, iterations=32):
    if force_fork and hasattr(subprocess, "_USE_VFORK"):
        subprocess._USE_VFORK = False
    kwargs = {"preexec_fn": (lambda: None)} if preexec_fn else {}
    codes = []
    for _ in range(iterations):
        codes.append(subprocess.run(["true"], timeout=PARENT_DEADLINE, **kwargs).returncode)
    return {"parent": harness.parent_observe(), "child": {"exit_ok": all(c == 0 for c in codes), "count": len(codes)}}


# Seccomp filter that makes process creation fail with EAGAIN while still allowing threads.
_SECCOMP_ARCH = {"x86_64": (0xC000003E, {"fork": 57, "vfork": 58, "clone": 56}), "aarch64": (0xC00000B7, {"clone": 220})}
_CLONE3 = 435
_CLONE_THREAD = 0x00010000


def install_fork_failure_filter():
    machine = platform.machine()
    if sys.platform != "linux" or machine not in _SECCOMP_ARCH:
        return "unsupported platform %s/%s" % (sys.platform, machine)
    arch, nrs = _SECCOMP_ARCH[machine]
    ret_allow = 0x7FFF0000
    ret_eagain = 0x00050000 | errno.EAGAIN
    ret_enosys = 0x00050000 | errno.ENOSYS
    ld_w_abs, jeq_k, jset_k, ret_k = 0x20, 0x15, 0x45, 0x06

    # (code, jt label, jf label, k); labels are resolved to relative offsets below.
    prog = [("ld", ld_w_abs, None, None, 4), ("arch", jeq_k, "next", "allow", arch), ("ld_nr", ld_w_abs, None, None, 0)]
    prog.append(("clone3", jeq_k, "enosys", "next", _CLONE3))
    for name in ("fork", "vfork"):
        if name in nrs:
            prog.append((name, jeq_k, "eagain", "next", nrs[name]))
    prog.append(("clone", jeq_k, "next", "allow", nrs["clone"]))
    # args[0] (clone flags) low word on little-endian
    prog.append(("flags", ld_w_abs, None, None, 16))
    prog.append(("thread", jset_k, "allow", "eagain", _CLONE_THREAD))
    prog.append(("allow", ret_k, None, None, ret_allow))
    prog.append(("eagain", ret_k, None, None, ret_eagain))
    prog.append(("enosys", ret_k, None, None, ret_enosys))
    index = {name: i for i, (name, *_rest) in enumerate(prog)}

    def offset(i, label):
        if label is None or label == "next":
            return 0
        return index[label] - i - 1

    raw = b"".join(
        struct.pack("HBBI", code, offset(i, jt), offset(i, jf), k) for i, (_n, code, jt, jf, k) in enumerate(prog)
    )
    buf = ctypes.create_string_buffer(raw)

    class SockFprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]

    fprog = SockFprog(len(prog), ctypes.addressof(buf))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        return "PR_SET_NO_NEW_PRIVS failed: %s" % os.strerror(ctypes.get_errno())
    if libc.prctl(22, 2, ctypes.byref(fprog), 0, 0) != 0:  # PR_SET_SECCOMP, SECCOMP_MODE_FILTER
        return "seccomp filter failed: %s" % os.strerror(ctypes.get_errno())
    return None


def scenario_failed(harness, kind):
    unsupported = install_fork_failure_filter()
    if unsupported:
        return {"skip": unsupported}
    try:
        pid = FORKS[kind]()
    except OSError as e:
        failed = e.errno == errno.EAGAIN
    else:
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        failed = False
    return {"parent": dict(harness.parent_observe(), fork_failed=failed), "child": {}}


SCENARIOS = {
    "os_fork": lambda h: scenario_single(h, "os"),
    "libc_fork": lambda h: scenario_single(h, "libc"),
    "os_fork_rebuild_first": lambda h: scenario_single(h, "os", rebuild_first=True),
    "subprocess_default": lambda h: scenario_exec(h),
    "subprocess_forced_fork": lambda h: scenario_exec(h, force_fork=True),
    "subprocess_preexec_fn": lambda h: scenario_exec(h, preexec_fn=True),
    "nested_os_os": lambda h: scenario_nested(h, "os", "os"),
    "nested_os_libc": lambda h: scenario_nested(h, "os", "libc"),
    "nested_libc_os": lambda h: scenario_nested(h, "libc", "os"),
    "nested_libc_os_native_only": lambda h: scenario_nested(h, "libc", "os", forget_dead_threads=True),
    "failed_os_fork": lambda h: scenario_failed(h, "os"),
    "failed_libc_fork": lambda h: scenario_failed(h, "libc"),
    "threads_os_fork": lambda h: scenario_threads(h, "os"),
    "threads_libc_fork": lambda h: scenario_threads(h, "libc"),
}


def main():
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    name = sys.argv[1]
    harness = Harness()
    report = SCENARIOS[name](harness)
    report["scenario"] = name
    sys.stdout.write("\n" + json.dumps(report) + "\n")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
