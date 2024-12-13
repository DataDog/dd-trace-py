from concurrent.futures import ThreadPoolExecutor
import http
import json
import os
import subprocess
import sys
import time

from ddtrace import config
from ddtrace.vendor import psutil


# Host on which the sidecar server will run
# Using localhost here prevents outside connections to the sidecar server.
SIDECAR_HOST = "127.0.0.1"
# Create a ThreadPoolExecutor with a maximum of 10 workers
executor = ThreadPoolExecutor(max_workers=10)

SIDECAR_PROCESS = None


def kill_process_on_port(port):
    for proc in psutil.process_iter(attrs=["pid", "name", "connections"]):
        try:
            if not proc.info["connections"]:
                continue
            # Iterate through the connections of each process
            for conn in proc.info["connections"]:
                # Check if the connection is in the listening state and on the desired port
                if conn.status == "LISTEN" and conn.laddr.port == int(port):
                    print(f"Killing process {proc.info['name']} with PID {proc.info['pid']} on port {port}")
                    proc.terminate()  # Terminate the process
                    proc.wait()  # Wait for the process to terminate
                    return
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


def start_sidecar_server():
    global SIDECAR_PROCESS
    kill_process_on_port(config._trace_sidecar_port)
    env = os.environ.copy()
    env["DD_TRACE_LOW_CPU_MODE"] = "false"
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "false"
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "false"
    env["DD_TRACE_DEBUG"] = "true"

    SIDECAR_PROCESS = subprocess.Popen(
        [sys.executable, "ddtrace/ddsidecar.py"],
        stdout=sys.stdout,
        stderr=sys.stderr,
        close_fds=True,
        env=env,
        preexec_fn=os.setsid,
        cwd=str(os.path.dirname(os.path.dirname(__file__))),
    )
    time.sleep(1)
    assert is_sidecar_alive(), "sidecar failed to start"


def is_sidecar_alive():
    # Send a GET request to the root endpoint
    conn = http.client.HTTPConnection("localhost", config._trace_sidecar_port)
    conn.request("POST", "/alive", body="{}")
    response = conn.getresponse()
    return response.status == 200


def send_request_threadpool(path, body):
    # Create a connection to the local server
    conn = http.client.HTTPConnection("localhost", config._trace_sidecar_port)
    # Send a POST request to the path with the body
    conn.request("POST", path, body=json.dumps(body))
    # Get the response
    response = conn.getresponse()
    response_str = response.read().decode("utf-8")
    # Print the response status and body for debugging
    assert response.status == 200, f"Response Status: {response.status}, Response Body: {response_str}"
    conn.close()


def update_sidecar(path, body):
    # Create a connection to the local server
    # Submit the request function for asynchronous execution
    executor.submit(send_request_threadpool, path, body)
