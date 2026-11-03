import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("enabled", ["true", "false"])
@pytest.mark.parametrize(
    "statement",
    [
        "import ddtrace._initialize",
        "from ddtrace.trace import tracer",
        "from ddtrace import patch; patch()",
        "import ddtrace.auto",
    ],
)
def test_shared_initialization(statement, enabled):
    code = f"""
import importlib.util
import sys
import ddtrace
assert "ddtrace.internal.datastreams" not in sys.modules
{statement}
assert ("ddtrace.internal.datastreams" in sys.modules) == {enabled == "true"}
if {enabled == "true"}:
    from ddtrace.internal import datastreams
    assert datastreams._processor is None
    if importlib.util.find_spec("botocore") is not None:
        from ddtrace.internal import core
        assert core.has_listeners("botocore.kinesis.update_record")
else:
    assert not any(name.startswith("ddtrace.internal.datastreams.") for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=dict(os.environ, DD_DATA_STREAMS_ENABLED=enabled),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_shared_initialization_does_not_initialize_tracing():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import ddtrace._initialize; "
            "assert 'ddtrace.internal.datastreams' in sys.modules; "
            "assert 'ddtrace.trace' not in sys.modules",
        ],
        env=dict(os.environ, DD_DATA_STREAMS_ENABLED="true"),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
