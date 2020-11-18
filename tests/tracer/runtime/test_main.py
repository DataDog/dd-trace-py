import os
import re
import subprocess


def call_program(*args):
    subp = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
    )
    stdout, stderr = subp.communicate()
    return stdout, stderr, subp.wait(), subp.pid


def test_call_script(monkeypatch):
    monkeypatch.setenv("DATADOG_TRACE_DEBUG", "1")
    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "true")
    stdout, stderr, exitcode, pid = call_program(
        "ddtrace-run", "python", os.path.join(os.path.dirname(__file__), "simple_program.py")
    )
    assert exitcode == 42
    output = stdout.decode().strip()
    assert output == "hello world"

    # check to make sure runtime worker is properly shutdown
    debug = stderr.decode().strip()
    assert re.search(r"Waiting \d+ seconds for RuntimeWorker to finish", debug)
