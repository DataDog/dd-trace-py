from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


# DEV: This test must pass ALWAYS. If this test fails, it means that something
# needs to be fixed somewhere. Please DO NOT skip this test under any
# circumstance!
@pytest.mark.subprocess(
    env=dict(
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="true",
    ),
    parametrize={
        "DD_REMOTE_CONFIGURATION_ENABLED": ("1", "0"),
    },
)
def test_auto():
    import os
    import sys

    import ddtrace.auto  # noqa:F401

    assert "threading" not in sys.modules
    assert "socket" not in sys.modules
    assert "subprocess" not in sys.modules

    if os.getenv("DD_REMOTE_CONFIGURATION_ENABLED") == "0":
        # When unloading modules we must have the HTTP clients imported already
        assert "ddtrace.internal.http" in sys.modules

        # emulate socket patching (e.g. by gevent)
        import socket  # noqa:F401

        socket.create_connection = None
        socket.socket = None

        from ddtrace.internal.http import HTTPConnection  # noqa:F401
        import ddtrace.internal.uds as uds

        assert HTTPConnection("localhost")._create_connection is not None
        assert uds.socket.socket is not None


@pytest.mark.skipif(sys.version_info < (3, 8), reason="Test requires Python 3.8+")
def test_pytest_with_gevent_and_ddtrace_auto():
    """
    Test that pytest works when a module imports ddtrace.auto and gevent is installed.

    This is a regression test for an issue where cleanup_loaded_modules() would delete
    modules from sys.modules while they were still being imported by pytest's assertion
    rewriter, causing a KeyError during import.

    The fix checks if a module's __spec__._initializing is True before deleting it.
    """
    pytest.importorskip("pytest")
    pytest.importorskip("gevent")

    # Create a temporary directory with test files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create a module that imports ddtrace.auto
        foo_module = tmpdir_path / "foo.py"
        foo_module.write_text(
            """import ddtrace.auto
A = 1
"""
        )

        # Create a test file that imports the module
        test_file = tmpdir_path / "test_foo.py"
        test_file.write_text(
            """from foo import A

def test_foo():
    assert A == 1
"""
        )

        # Run pytest as a subprocess with the current ddtrace on PYTHONPATH
        import os

        import ddtrace

        ddtrace_path = str(Path(ddtrace.__file__).parent.parent)
        env = os.environ.copy()
        # Prepend ddtrace path to PYTHONPATH to ensure we use the local version
        env["PYTHONPATH"] = ddtrace_path + os.pathsep + env.get("PYTHONPATH", "")

        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-v"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            env=env,
        )

        # The test should pass without KeyError
        assert result.returncode == 0, f"pytest failed:\nSTDOUT:\n{result.stdout}\n\nSTDERR:\n{result.stderr}"
        assert "KeyError" not in result.stdout
        assert "KeyError" not in result.stderr
        assert "1 passed" in result.stdout
