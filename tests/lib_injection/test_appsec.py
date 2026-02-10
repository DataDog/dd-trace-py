"""
Tests for AppSec enablement via Kubernetes library injection.

These tests validate that AppSec:
1. Is enabled via DD_APPSEC_ENABLED=true (explicit)
2. Is NOT enabled via DD_INJECTION_ENABLED token (unlike profiler)
3. Works correctly with Kubernetes DatadogAgent configuration
"""

import subprocess

import pytest


def test_appsec_enabled_with_explicit_env_var(test_venv, mock_telemetry_forwarder):
    """
    Tests that AppSec is enabled when DD_APPSEC_ENABLED=true is set explicitly.

    This is the only way to enable AppSec - it must be set as an environment
    variable in the Kubernetes pod configuration.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # AppSec requires explicit environment variable
    env["DD_APPSEC_ENABLED"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import asm

    print(f"DD_APPSEC_ENABLED={os.environ.get('DD_APPSEC_ENABLED')}")
    print(f"asm.config._asm_enabled={asm.config._asm_enabled}")

    if not asm.config._asm_enabled:
        print("ERROR: AppSec should be enabled with DD_APPSEC_ENABLED=true", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: AppSec correctly enabled with explicit env var")
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

        assert "SUCCESS: AppSec correctly enabled with explicit env var" in stdout, (
            f"AppSec was not enabled with DD_APPSEC_ENABLED=true.\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
        assert "asm.config._asm_enabled=True" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_appsec_not_enabled_via_injection_token(test_venv, mock_telemetry_forwarder):
    """
    Tests that AppSec is NOT enabled via DD_INJECTION_ENABLED token.

    Unlike profiler, AppSec does NOT support being enabled via DD_INJECTION_ENABLED.
    This is a key difference between profiler and AppSec configuration.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # Try to enable AppSec via DD_INJECTION_ENABLED (should NOT work)
    env["DD_INJECTION_ENABLED"] = "service_name,tracer,appsec"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    # DD_APPSEC_ENABLED not set
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import asm

    print(f"DD_INJECTION_ENABLED={os.environ.get('DD_INJECTION_ENABLED')}")
    print(f"DD_APPSEC_ENABLED={os.environ.get('DD_APPSEC_ENABLED', 'not set')}")
    print(f"asm.config._asm_enabled={asm.config._asm_enabled}")

    # AppSec should NOT be enabled via DD_INJECTION_ENABLED
    if asm.config._asm_enabled:
        print("ERROR: AppSec should NOT be enabled via DD_INJECTION_ENABLED token", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: AppSec correctly NOT enabled via injection token")
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
        assert "SUCCESS: AppSec correctly NOT enabled via injection token" in stdout, (
            f"AppSec should not be enabled via DD_INJECTION_ENABLED.\nstdout:\n{stdout}"
        )
        assert "asm.config._asm_enabled=False" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_appsec_not_enabled_by_default(test_venv, mock_telemetry_forwarder):
    """
    Tests that AppSec is NOT enabled by default when no configuration is provided.

    AppSec should be opt-in, not opt-out.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # No AppSec configuration
    env["DD_INJECTION_ENABLED"] = "service_name,tracer"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import asm

    print(f"DD_APPSEC_ENABLED={os.environ.get('DD_APPSEC_ENABLED', 'not set')}")
    print(f"asm.config._asm_enabled={asm.config._asm_enabled}")

    if asm.config._asm_enabled:
        print("ERROR: AppSec should NOT be enabled by default", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: AppSec correctly NOT enabled by default")
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
        assert "SUCCESS: AppSec correctly NOT enabled by default" in stdout
        assert "asm.config._asm_enabled=False" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_kubernetes_datadog_agent_config_full(test_venv, mock_telemetry_forwarder):
    """
    Tests the full Kubernetes DatadogAgent configuration with both profiler and AppSec.

    This simulates the configuration:
    ```yaml
    ddTraceConfigs:
      - name: "DD_PROFILING_ENABLED"
        value: "auto"
      - name: "DD_APPSEC_ENABLED"
        value: "true"
    asm:
      threats:
        enabled: true
    ```

    Plus DD_INJECTION_ENABLED set by the admission controller.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # Full Kubernetes DatadogAgent configuration
    env["DD_PROFILING_ENABLED"] = "auto"
    env["DD_APPSEC_ENABLED"] = "true"
    env["DD_INJECTION_ENABLED"] = "service_name,tracer,profiler"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import profiling, asm

    print("=== Configuration ===")
    print(f"DD_INJECTION_ENABLED={os.environ.get('DD_INJECTION_ENABLED')}")
    print(f"DD_PROFILING_ENABLED={os.environ.get('DD_PROFILING_ENABLED')}")
    print(f"DD_APPSEC_ENABLED={os.environ.get('DD_APPSEC_ENABLED')}")
    print()
    print("=== Results ===")
    print(f"profiling.config.enabled={profiling.config.enabled}")
    print(f"asm.config._asm_enabled={asm.config._asm_enabled}")

    # Both should be enabled
    if not profiling.config.enabled:
        print("ERROR: Profiling should be enabled", file=sys.stderr)
        sys.exit(1)

    if not asm.config._asm_enabled:
        print("ERROR: AppSec should be enabled", file=sys.stderr)
        sys.exit(1)

    print()
    print("SUCCESS: Both profiling and AppSec enabled as expected from Kubernetes config")
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

        assert "SUCCESS: Both profiling and AppSec enabled as expected from Kubernetes config" in stdout, (
            f"Full Kubernetes config test failed.\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
        assert "profiling.config.enabled=True" in stdout
        assert "asm.config._asm_enabled=True" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_appsec_enabled_value_variations(test_venv, mock_telemetry_forwarder):
    """
    Tests that DD_APPSEC_ENABLED accepts various truthy values.

    Valid values should be: true, TRUE, 1, yes, on (case-insensitive)
    Unlike profiler, AppSec does NOT accept 'auto' as a value.
    """
    packages_to_install = {}

    valid_values = ["true", "TRUE", "True", "1", "yes", "YES"]

    for value in valid_values:
        python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

        env = base_env.copy()
        env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"
        env["DD_APPSEC_ENABLED"] = value
        env["DD_INJECTION_ENABLED"] = "true"
        env["DD_TRACE_DEBUG"] = "true"
        env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
        _ = mock_telemetry_forwarder(env, python_executable)

        script = f"""
import os
import sys

try:
    from ddtrace.internal.settings import asm

    value = "{value}"
    print(f"Testing DD_APPSEC_ENABLED={{value}}")
    print(f"asm.config._asm_enabled={{asm.config._asm_enabled}}")

    if not asm.config._asm_enabled:
        print(f"ERROR: AppSec should be enabled with DD_APPSEC_ENABLED={{value}}", file=sys.stderr)
        sys.exit(1)

    print(f"SUCCESS: AppSec enabled with value={{value}}")
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
            assert f"SUCCESS: AppSec enabled with value={value}" in stdout, (
                f"AppSec was not enabled with DD_APPSEC_ENABLED={value}.\nstdout:\n{stdout}"
            )

        except subprocess.CalledProcessError as e:
            pytest.fail(
                f"Test failed for DD_APPSEC_ENABLED={value}:\n"
                f"Exit code: {e.returncode}\n"
                f"stdout:\n{e.stdout}\n"
                f"stderr:\n{e.stderr}"
            )


def test_appsec_not_enabled_with_auto_value(test_venv, mock_telemetry_forwarder):
    """
    Tests that DD_APPSEC_ENABLED=auto does NOT enable AppSec.

    Unlike profiler, AppSec does not support the 'auto' value. This is a key
    difference between profiler and AppSec configuration behavior.
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # Try to enable AppSec with 'auto' value (should NOT work)
    env["DD_APPSEC_ENABLED"] = "auto"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import asm

    print(f"DD_APPSEC_ENABLED={os.environ.get('DD_APPSEC_ENABLED')}")
    print(f"asm.config._asm_enabled={asm.config._asm_enabled}")

    # AppSec should NOT be enabled with 'auto' value
    if asm.config._asm_enabled:
        print("ERROR: AppSec should NOT be enabled with DD_APPSEC_ENABLED=auto", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: AppSec correctly NOT enabled with 'auto' value")
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
        assert "SUCCESS: AppSec correctly NOT enabled with 'auto' value" in stdout, (
            f"AppSec should not accept 'auto' as a value.\nstdout:\n{stdout}"
        )
        assert "asm.config._asm_enabled=False" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_appsec_with_iast_enabled(test_venv, mock_telemetry_forwarder):
    """
    Tests that both AppSec and IAST can be enabled together via explicit env vars.

    This validates the Kubernetes configuration where both AppSec features are enabled:
    ```yaml
    ddTraceConfigs:
      - name: "DD_APPSEC_ENABLED"
        value: "true"
      - name: "DD_IAST_ENABLED"
        value: "true"
    ```
    """
    packages_to_install = {}
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv(packages_to_install)

    env = base_env.copy()
    env["PYTHONPATH"] = f"{sitecustomize_dir}{':'}{base_env.get('PYTHONPATH', '')}"

    # Enable both AppSec and IAST
    env["DD_APPSEC_ENABLED"] = "true"
    env["DD_IAST_ENABLED"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    _ = mock_telemetry_forwarder(env, python_executable)

    script = """
import os
import sys

try:
    from ddtrace.internal.settings import asm

    print(f"DD_APPSEC_ENABLED={os.environ.get('DD_APPSEC_ENABLED')}")
    print(f"DD_IAST_ENABLED={os.environ.get('DD_IAST_ENABLED')}")
    print(f"asm.config._asm_enabled={asm.config._asm_enabled}")
    print(f"asm.config._iast_enabled={asm.config._iast_enabled}")

    if not asm.config._asm_enabled:
        print("ERROR: AppSec should be enabled", file=sys.stderr)
        sys.exit(1)

    if not asm.config._iast_enabled:
        print("ERROR: IAST should be enabled", file=sys.stderr)
        sys.exit(1)

    print("SUCCESS: Both AppSec and IAST enabled together")
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
        assert "SUCCESS: Both AppSec and IAST enabled together" in stdout
        assert "asm.config._asm_enabled=True" in stdout
        assert "asm.config._iast_enabled=True" in stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed with exit code {e.returncode}:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")
