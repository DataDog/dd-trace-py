"""Test compatibility with old ddtrace.profile name."""
import subprocess
import os


def test_call_script():
    subp = subprocess.Popen(
        ["python", os.path.join(os.path.dirname(__file__), "compat_program.py")], stdout=subprocess.PIPE
    )
    stdout, stderr = subp.communicate()
    assert subp.wait() == 42
    hello, interval, stacks = stdout.decode().strip().split("\n")
    assert hello == "hello world"
    assert float(interval) >= 0.01
    assert int(stacks) >= 1
