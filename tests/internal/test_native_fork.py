import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS resolver fork handlers")
def test_native_atfork_does_not_deadlock_with_dns_lookup():
    """Fork preparation must not wait for a worker blocked by the macOS resolver lock."""
    code = """
import asyncio
import os
import subprocess
import time

os.environ["DD_TRACE_AGENT_URL"] = f"http://ddtrace-repro-{os.getpid()}.local:8126"
os.environ["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = "0.05"
os.environ["DD_REMOTE_CONFIGURATION_ENABLED"] = "false"

import ddtrace

time.sleep(0.1)
print("before subprocess", flush=True)
subprocess.run(
    ["/usr/bin/true"],
    capture_output=True,
    check=True,
    timeout=3,
)
print("subprocess completed", flush=True)


async def _run_asyncio_subprocess():
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/true",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(process.wait(), timeout=3)


asyncio.run(_run_asyncio_subprocess())
print("asyncio subprocess completed", flush=True)
os._exit(0)
"""

    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"subprocess creation deadlocked; output before timeout: {error.stdout!r}")

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert b"subprocess completed" in completed.stdout, (completed.stdout, completed.stderr)
    assert b"asyncio subprocess completed" in completed.stdout, (completed.stdout, completed.stderr)
