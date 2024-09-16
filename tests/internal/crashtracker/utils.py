# Utility functions for testing crashtracker in subprocesses
import os
import random
import select
import socket


def crashtracker_receiver_bind():
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


def listen_get_conn(sock):
    """Given a listening socket, wait for a connection and return it"""
    if not sock:
        return None
    rlist, _, _ = select.select([sock], [], [], 5.0)  # 5 second timeout
    if not rlist:
        return None
    conn, _ = sock.accept()
    return conn


def conn_to_bytes(conn):
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


def start_crashtracker(port: int):
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
        crashtracker.set_stdout_filename("stdout.log")
        crashtracker.set_stderr_filename("stderr.log")
        crashtracker.set_alt_stack(False)
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
