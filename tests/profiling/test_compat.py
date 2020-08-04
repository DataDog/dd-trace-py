"""Test compatibility with old ddtrace.profile name."""
import subprocess
import os


def test_call_script(monkeypatch):
    # Set a very short timeout to exit fast
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    subp = subprocess.Popen(
        ["python", os.path.join(os.path.dirname(__file__), "compat_program.py")], stdout=subprocess.PIPE
    )
    stdout, stderr = subp.communicate()
    assert subp.wait() == 42
    hello, interval, stacks = stdout.decode().strip().split("\n")
    assert hello == "hello world"
    assert float(interval) >= 0.01
    assert int(stacks) >= 1
