# Utility functions for testing crashtracker in subprocesses
import os
import random
import select
import socket
from typing import Optional
from typing import Tuple

import pytest


def crashtracker_receiver_bind() -> Tuple[int, socket.socket]:
    """Bind to a random port in the range 10000-19999"""
    port = None
    sock = None
    for i in range(5):
        port = 10000
        port += random.randint(0, 9999)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("localhost", port))
            sock.listen(1)
            break
        except Exception:
            port = None
            sock = None
    return port, sock


def listen_get_conn(sock: socket.socket) -> Optional[socket.socket]:
    """Given a listening socket, wait for a connection and return it"""
    if not sock:
        return None
    rlist, _, _ = select.select([sock], [], [], 5.0)  # 5 second timeout
    if not rlist:
        return None
    conn, _ = sock.accept()
    return conn


def conn_to_bytes(conn: socket.socket) -> bytes:
    """Read all data from a connection and return it"""
    # Don't assume nonblocking socket, so go back up to select for everything
    ret = b""
    while True:
        rlist, _, _ = select.select([conn], [], [], 1.0)
        if not rlist:
            break
        msg = conn.recv(4096)
        if not msg:
            break
        ret += msg
    return ret


def start_crashtracker(port: int, stdout: Optional[str] = None, stderr: Optional[str] = None) -> bool:
    """Start the crashtracker with some placeholder values"""
    ret = False
    try:
        import ddtrace.internal.datadog.profiling.crashtracker as crashtracker

        crashtracker.set_url("http://localhost:%d" % port)
        crashtracker.set_service("my_favorite_service")
        crashtracker.set_version("v0.0.0.0.0.0.1")
        crashtracker.set_runtime("shmython")
        crashtracker.set_runtime_version("v9001")
        crashtracker.set_runtime_id("0")
        crashtracker.set_library_version("v2.7.1.8")
        crashtracker.set_stdout_filename(stdout)
        crashtracker.set_stderr_filename(stderr)
        crashtracker.set_resolve_frames_full()
        ret = crashtracker.start()
    except Exception as e:
        print("Failed to start crashtracker: %s" % str(e))
        ret = False
    return ret


def read_files(files):
    msg = []
    for file in files:
        this_msg = ""
        if os.path.exists(file):
            with open(file, "r") as f:
                this_msg = f.read()
        msg.append(this_msg)
    return msg


def set_cerulean_mollusk():
    """
    Many crashtracking tests deal with the behavior of the init process in a given PID namespace.
    For testing, it's useful to designate a process as the subreaper via `PR_SET_CHILD_SUBREAPER`.
    This function sets the current process as the subreaper.
    There is no need to fear this function.
    """
    try:
        import ctypes
        import ctypes.util

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        PR_SET_CHILD_SUBREAPER = 36  # from <linux/prctl.h>

        # Now setup the prctl definition
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int

        result = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        if result != 0:
            return False
    except Exception as e:
        print("Failed to set subreaper: %s" % str(e))
        return False
    return True


# A class that wraps start_crashtracker and maintains its own logfiles
class CrashtrackerWrapper:
    _seed = 0

    def __init__(self, port: int, base_name=""):
        if CrashtrackerWrapper._seed == 0:
            CrashtrackerWrapper._seed = random.randint(0, 999999)

        self.port = port
        self.stdout = f"stdout.{base_name}.{CrashtrackerWrapper._seed}.log"
        self.stderr = f"stderr.{base_name}.{CrashtrackerWrapper._seed}.log"

        for file in [self.stdout, self.stderr]:
            if os.path.exists(file):
                os.unlink(file)

    def __del__(self):
        for file in [self.stdout, self.stderr]:
            if os.path.exists(file):
                os.unlink(file)

    def get_filenames(self):
        return [self.stdout, self.stderr]

    def start(self):
        return start_crashtracker(self.port, self.stdout, self.stderr)

    def logs(self):
        return read_files([self.stdout, self.stderr])


def get_crash_report(sock: socket.socket) -> bytes:
    """Wait for a crash report from the crashtracker listener socket."""
    conn = listen_get_conn(sock)
    assert conn

    try:
        for _ in range(5):
            data = conn_to_bytes(conn)
            assert data, "Expected data from crashreport listener, got empty response"

            # Ignore any /info requests, which might occur before the crash report is sent
            if data.startswith(b"GET /info"):
                continue

            return data

        else:
            pytest.fail("No crash report received after 5 attempts")
    finally:
        conn.close()
