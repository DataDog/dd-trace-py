"""
Tests for profiler enablement via Kubernetes library injection.

These tests validate that the profiler can be enabled through:
1. DD_INJECTION_ENABLED containing "profiler" token
2. DD_PROFILING_ENABLED="auto" value
3. Combination of both (as set by Kubernetes DatadogAgent)
"""

import subprocess

import pytest


def test_profiling_enabled_via_injection_token(test_venv, mock_telemetry_forwarder):
    """
    Tests that profiling is enabled when DD_INJECTION_ENABLED contains 'profiler'.

    This simulates the Kubernetes admission controller setting DD_INJECTION_ENABLED
    with the 'profiler' token, which should enable profiling even without
    DD_PROFILING_ENABLED being set.
    """
    packages_to_install = {}
    venv_factory_func = test_venv
    python_executable, sitecustomize_dir, base_env, venv_dir = venv_factory_func(packages_to_install)

    env = base_env.copy()
    venv_pythonpath = base_env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{venv_pythonpath}"

    # Simulate Kubernetes admission controller setting DD_INJECTION_ENABLED with profiler token
    env["DD_INJECTION_ENABLED"] = "service_name,tracer,profiler"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_INJECT_FORCE"] = "false"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import profiling

    # Check that profiling is enabled via DD_INJECTION_ENABLED
    print(f"DD_INJECTION_ENABLED={os.environ.get('DD_INJECTION_ENABLED')}")
    print(f"DD_PROFILING_ENABLED={os.environ.get('DD_PROFILING_ENABLED', 'not set')}")
    print(f"profiling.config.enabled={profiling.config.enabled}")

    if not profiling.config.enabled:
        print("ERROR: Profiling should be enabled via DD_INJECTION_ENABLED", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: Profiling correctly enabled via injection token")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""

    try:
        result = subprocess.run(
            [python_executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=venv_dir,
            check=True,
            timeout=3,
        )
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.CalledProcessError as e:
        stdout = e.stdout
        stderr = e.stderr
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout
        stderr = e.stderr

    assert "SUCCESS: Profiling correctly enabled via injection token" in stdout, (
        f"Profiling was not enabled via DD_INJECTION_ENABLED.\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
    assert "profiling.config.enabled=True" in stdout, f"profiling.config.enabled should be True.\nstdout:\n{stdout}"

def test_profiling_enabled_with_auto_value(test_venv, mock_telemetry_forwarder):
    """
    Tests that DD_PROFILING_ENABLED=auto enables profiling.

    This is the configuration that Kubernetes DatadogAgent sets directly
    via the ddTraceConfigs field.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"
    env["DD_PROFILING_ENABLED"] = "auto"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import profiling

    print(f"DD_PROFILING_ENABLED={os.environ.get('DD_PROFILING_ENABLED')}")
    print(f"profiling.config.enabled={profiling.config.enabled}")

    if not profiling.config.enabled:
        print("ERROR: Profiling should be enabled with DD_PROFILING_ENABLED=auto", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: Profiling enabled with auto value")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""

    try:
        result = subprocess.run(
            [python_executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=180,
        )

        stdout = result.stdout
        stderr = result.stderr

        assert "SUCCESS: Profiling enabled with auto value" in stdout, (
            f"Profiling was not enabled with DD_PROFILING_ENABLED=auto.\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
        assert "profiling.config.enabled=True" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_profiling_enabled_with_both_configs(test_venv, mock_telemetry_forwarder):
    """
    Tests profiling enablement with both DD_INJECTION_ENABLED and DD_PROFILING_ENABLED.

    This simulates the full Kubernetes configuration where:
    - Admission controller sets DD_INJECTION_ENABLED with 'profiler'
    - DatadogAgent config sets DD_PROFILING_ENABLED='auto'

    Both mechanisms should work together.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # Full Kubernetes configuration
    env["DD_INJECTION_ENABLED"] = "service_name,tracer,profiler"
    env["DD_PROFILING_ENABLED"] = "auto"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import profiling

    print(f"DD_INJECTION_ENABLED={os.environ.get('DD_INJECTION_ENABLED')}")
    print(f"DD_PROFILING_ENABLED={os.environ.get('DD_PROFILING_ENABLED')}")
    print(f"profiling.config.enabled={profiling.config.enabled}")

    if not profiling.config.enabled:
        print("ERROR: Profiling should be enabled with both configs", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: Profiling enabled with both DD_INJECTION_ENABLED and DD_PROFILING_ENABLED")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""

    try:
        result = subprocess.run(
            [python_executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=180,
        )

        stdout = result.stdout
        assert "SUCCESS: Profiling enabled with both DD_INJECTION_ENABLED and DD_PROFILING_ENABLED" in stdout
        assert "profiling.config.enabled=True" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_profiling_not_enabled_without_config(test_venv, mock_telemetry_forwarder):
    """
    Tests that profiling is NOT enabled when neither DD_INJECTION_ENABLED
    nor DD_PROFILING_ENABLED is set properly.

    This ensures the default behavior is correct.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # No profiling configuration
    env["DD_INJECTION_ENABLED"] = "service_name,tracer"  # No 'profiler' token
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    # DD_PROFILING_ENABLED not set
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import profiling

    print(f"DD_INJECTION_ENABLED={os.environ.get('DD_INJECTION_ENABLED')}")
    print(f"DD_PROFILING_ENABLED={os.environ.get('DD_PROFILING_ENABLED', 'not set')}")
    print(f"profiling.config.enabled={profiling.config.enabled}")

    if profiling.config.enabled:
        print("ERROR: Profiling should NOT be enabled without proper config", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: Profiling correctly NOT enabled without config")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""

    try:
        result = subprocess.run(
            [python_executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=180,
        )

        stdout = result.stdout
        assert "SUCCESS: Profiling correctly NOT enabled without config" in stdout
        assert "profiling.config.enabled=False" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_profiling_not_enabled_with_wrong_injection_token(test_venv, mock_telemetry_forwarder):
    """
    Tests that profiling is NOT enabled when DD_INJECTION_ENABLED is set
    but doesn't contain the 'profiler' token.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # DD_INJECTION_ENABLED without 'profiler' token
    env["DD_INJECTION_ENABLED"] = "service_name,tracer,appsec"  # Note: no 'profiler'
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import profiling

    print(f"DD_INJECTION_ENABLED={os.environ.get('DD_INJECTION_ENABLED')}")
    print(f"profiling.config.enabled={profiling.config.enabled}")

    if profiling.config.enabled:
        print("ERROR: Profiling should NOT be enabled without 'profiler' token", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: Profiling correctly NOT enabled without 'profiler' token")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""

    try:
        result = subprocess.run(
            [python_executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=180,
        )

        stdout = result.stdout
        assert "SUCCESS: Profiling correctly NOT enabled without 'profiler' token" in stdout
        assert "profiling.config.enabled=False" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_profiling_enabled_value_variations(test_venv, mock_telemetry_forwarder):
    """
    Tests that DD_PROFILING_ENABLED accepts various truthy values including 'auto'.

    Valid values should be: 1, true, yes, on, auto (case-insensitive)
    """
    packages_to_install = {}

    valid_values = ["auto", "AUTO", "Auto", "true", "TRUE", "1", "yes", "YES", "on", "ON"]

    for value in valid_values:
        python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

        env = base_env.copy()
        env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"
        env["DD_PROFILING_ENABLED"] = value
        env["DD_INJECTION_ENABLED"] = "true"
        env["DD_TRACE_DEBUG"] = "true"
        env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
        _ = mock_telemetry_forwarder(env, python_executable)

        script = f"""
import os
import sys

try:
    from ddtrace.internal.settings import profiling

    value = "{value}"
    print(f"Testing DD_PROFILING_ENABLED={{value}}")
    print(f"profiling.config.enabled={{profiling.config.enabled}}")

    if not profiling.config.enabled:
        print(f"ERROR: Profiling should be enabled with DD_PROFILING_ENABLED={{value}}", file=sys.stderr)
        sys.exit(1)

    print(f"SUCCESS: Profiling enabled with value={{value}}")
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""

        try:
            result = subprocess.run(
                [python_executable, "-c", script],
                capture_output=True,
                text=True,
                env=env,
                check=True,
                timeout=180,
            )

            stdout = result.stdout
            assert f"SUCCESS: Profiling enabled with value={value}" in stdout, (
                f"Profiling was not enabled with DD_PROFILING_ENABLED={value}.\nstdout:\n{stdout}"
            )

        except subprocess.CalledProcessError as e:
            pytest.fail(
                f"Test failed for DD_PROFILING_ENABLED={value}:\n"
                f"Exit code: {e.returncode}\n"
                f"stdout:\n{e.stdout}\n"
                f"stderr:\n{e.stderr}"
            )
