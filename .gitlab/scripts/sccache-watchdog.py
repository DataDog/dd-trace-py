#!/usr/bin/env python3
"""Capture one snapshot of a stalled macOS build before the job timeout."""

import argparse
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def stop(signum: int, frame: object) -> None:
    raise SystemExit(0)


def probe(command: list[str], timeout: float = 15) -> None:
    """Bound diagnostic commands and clean up only their own process group."""
    try:
        process = subprocess.Popen(command, start_new_session=True)
    except OSError as error:
        print(f"WARNING: diagnostic command unavailable: {command[0]}: {error}", flush=True)
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"WARNING: diagnostic command timed out: {command[0]}", flush=True)
    finally:
        # A probe can leave children behind even after its leader has exited.
        # These sessions belong to the watchdog, never to the build or cache server.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def build_log_tail() -> None:
    log_dir = Path(os.environ.get("CI_PROJECT_DIR", ".")) / "debugwheelhouse"
    try:
        latest = max(log_dir.glob("build_*.log"), key=lambda path: path.stat().st_mtime_ns, default=None)
    except OSError as error:
        print(f"WARNING: cannot find wheel build log: {error}", flush=True)
        return
    if latest is not None:
        print(f"--- latest wheel build log: {latest.name} (last 100 lines) ---", flush=True)
        probe(["tail", "-n", "100", str(latest)], timeout=5)


def snapshot(parent_pid: int) -> None:
    build_log_tail()
    probe(["bash", str(Path(__file__).with_name("sccache-diagnostics.sh")), "stalled"])
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,state=,pcpu=,etime=,comm="],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"WARNING: cannot inspect build processes: {error}", flush=True)
        return

    processes = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 5)
        if len(fields) == 6 and fields[0].isdigit() and fields[1].isdigit():
            processes[int(fields[0])] = (int(fields[1]), fields[5], line)

    descendants = {parent_pid}
    while True:
        children = {pid for pid, (ppid, _, _) in processes.items() if ppid in descendants}
        if children <= descendants:
            break
        descendants.update(children)

    print("--- build process tree (pid ppid state cpu elapsed executable) ---", flush=True)
    candidates = []
    for pid in sorted(descendants):
        if pid not in processes or pid == os.getpid():
            continue
        _, executable, line = processes[pid]
        print(line, flush=True)
        if Path(executable).name in {"sccache", "rustc", "clang", "clang++", "cargo", "uv"}:
            candidates.append(pid)

    sample = shutil.which("sample")
    if sample:
        # Limit both the number and duration of samples. Sampling is read-only;
        # cancelling a probe must never signal the process being sampled.
        for pid in candidates[-2:]:
            if os.getppid() != parent_pid:
                return
            print(f"--- stack sample for build process {pid} ---", flush=True)
            probe([sample, str(pid), "3", "10", "-file", "/dev/stdout"], timeout=10)


def watch(parent_pid: int, delay: float) -> None:
    deadline = time.monotonic() + delay
    while os.getppid() == parent_pid:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            snapshot(parent_pid)
            return
        time.sleep(min(remaining, 1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=30 * 60, help="Seconds before the one-shot snapshot")
    args = parser.parse_args()
    if not 0 <= args.delay < float("inf"):
        parser.error("delay must be finite and non-negative")
    # GitLab can evaluate script inside a subshell, where Bash's $$ still names
    # the outer shell. Observe our actual parent instead.
    parent_pid = os.getppid()
    if parent_pid <= 1:
        return
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"sccache watchdog armed: parent={parent_pid} snapshot_after={args.delay:g}s", flush=True)
    watch(parent_pid, args.delay)


if __name__ == "__main__":
    main()
