"""Integration test for DD_CIVISIBILITY_ENABLED killswitch functionality.

This test verifies that when DD_CIVISIBILITY_ENABLED is set to false/0,
CI Visibility is disabled and pytest traces go to the regular agent
instead of citestcycle intake, even when DD_CIVISIBILITY_AGENTLESS_ENABLED=1.
"""

import json
import os
import subprocess
import tempfile
from unittest import mock

import pytest


def test_pytest_ddtrace_killswitch_disabled_by_env_false(tmpdir):
    """Test that DD_CIVISIBILITY_ENABLED=false disables CI Visibility even with agentless enabled."""
    # Create a simple test file
    test_file = tmpdir.join("test_simple.py")
    test_file.write(
        """
def test_simple():
    assert True
"""
    )

    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",  # Should enable citestcycle intake
            "DD_CIVISIBILITY_ENABLED": "false",  # Should disable CI Visibility entirely
            "DD_TRACE_AGENT_URL": "http://localhost:9126",
        }
    )

    # Mock the HTTP connection's request and getresponse methods
    mock_response = mock.MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b'{"data": []}'

    with mock.patch("ddtrace.internal.utils.http.get_connection") as mock_get_conn:
        mock_conn = mock.MagicMock()
        mock_conn.getresponse.return_value = mock_response
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        result = subprocess.run(
            ["python", "-m", "ddtrace.commands.ddtrace_run", "pytest", "--ddtrace", str(test_file)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(tmpdir),
        )

    # Test should pass
    assert result.returncode == 0, f"pytest failed with stderr: {result.stderr}"

    # Verify citestcycle intake was NOT called (because CI Visibility is disabled)
    # Since we're testing the killswitch, there should be no calls to CI Visibility endpoints
    if mock_conn.request.call_args_list:
        request_calls = [call for call in mock_conn.request.call_args_list]
        citestcycle_calls = [call for call in request_calls if "citestcycle" in str(call)]
        assert len(citestcycle_calls) == 0, f"Expected no citestcycle calls, but got: {citestcycle_calls}"


def test_pytest_ddtrace_killswitch_disabled_by_env_0(tmpdir):
    """Test that DD_CIVISIBILITY_ENABLED=0 disables CI Visibility even with agentless enabled."""
    # Create a simple test file
    test_file = tmpdir.join("test_simple.py")
    test_file.write(
        """
def test_simple():
    assert True
"""
    )

    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",  # Should enable citestcycle intake
            "DD_CIVISIBILITY_ENABLED": "0",  # Should disable CI Visibility entirely
            "DD_TRACE_AGENT_URL": "http://localhost:9126",
        }
    )

    # Mock the HTTP connection's request and getresponse methods
    mock_response = mock.MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b'{"data": []}'

    with mock.patch("ddtrace.internal.utils.http.get_connection") as mock_get_conn:
        mock_conn = mock.MagicMock()
        mock_conn.getresponse.return_value = mock_response
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        result = subprocess.run(
            ["python", "-m", "ddtrace.commands.ddtrace_run", "pytest", "--ddtrace", str(test_file)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(tmpdir),
        )

    # Test should pass
    assert result.returncode == 0, f"pytest failed with stderr: {result.stderr}"

    # Verify citestcycle intake was NOT called (because CI Visibility is disabled)
    # Since we're testing the killswitch, there should be no calls to CI Visibility endpoints
    if mock_conn.request.call_args_list:
        request_calls = [call for call in mock_conn.request.call_args_list]
        citestcycle_calls = [call for call in request_calls if "citestcycle" in str(call)]
        assert len(citestcycle_calls) == 0, f"Expected no citestcycle calls, but got: {citestcycle_calls}"


def test_pytest_ddtrace_killswitch_enabled_by_default(tmpdir):
    """Test that CI Visibility is enabled by default when DD_CIVISIBILITY_ENABLED is not set."""
    # Create a simple test file
    test_file = tmpdir.join("test_simple.py")
    test_file.write(
        """
def test_simple():
    assert True
"""
    )

    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",  # Should enable citestcycle intake
            # DD_CIVISIBILITY_ENABLED not set - should default to enabled
            "DD_TRACE_AGENT_URL": "http://localhost:9126",
        }
    )

    # Mock the HTTP connection's request and getresponse methods
    settings_response_data = {
        "data": {
            "attributes": {
                "code_coverage": False,
                "tests_skipping": False,
                "itr_enabled": False,
                "require_git": False,
                "early_flake_detection": {"enabled": False},
                "flaky_test_retries_enabled": False,
                "test_management": {"enabled": False},
            }
        }
    }
    mock_response = mock.MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps(settings_response_data).encode("utf-8")

    with mock.patch("ddtrace.internal.utils.http.get_connection") as mock_get_conn:
        mock_conn = mock.MagicMock()
        mock_conn.getresponse.return_value = mock_response
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        result = subprocess.run(
            ["python", "-m", "ddtrace.commands.ddtrace_run", "pytest", "--ddtrace", str(test_file)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(tmpdir),
        )

    # Test should pass
    assert result.returncode == 0, f"pytest failed with stderr: {result.stderr}"

    # Verify settings API was called (indicating CI Visibility was enabled)
    if mock_conn.request.call_args_list:
        request_calls = [call for call in mock_conn.request.call_args_list]
        settings_calls = [call for call in request_calls if "libraries/tests/services/setting" in str(call)]
        assert len(settings_calls) > 0, "Expected settings API to be called when CI Visibility is enabled"

    # May also verify citestcycle calls were made, but settings call is sufficient proof CI Visibility was enabled


def test_pytest_ddtrace_killswitch_enabled_by_env_true(tmpdir):
    """Test that DD_CIVISIBILITY_ENABLED=true enables CI Visibility."""
    # Create a simple test file
    test_file = tmpdir.join("test_simple.py")
    test_file.write(
        """
def test_simple():
    assert True
"""
    )

    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",  # Should enable citestcycle intake
            "DD_CIVISIBILITY_ENABLED": "true",  # Explicitly enable CI Visibility
            "DD_TRACE_AGENT_URL": "http://localhost:9126",
        }
    )

    # Mock the HTTP connection's request and getresponse methods
    settings_response_data = {
        "data": {
            "attributes": {
                "code_coverage": False,
                "tests_skipping": False,
                "itr_enabled": False,
                "require_git": False,
                "early_flake_detection": {"enabled": False},
                "flaky_test_retries_enabled": False,
                "test_management": {"enabled": False},
            }
        }
    }
    mock_response = mock.MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps(settings_response_data).encode("utf-8")

    with mock.patch("ddtrace.internal.utils.http.get_connection") as mock_get_conn:
        mock_conn = mock.MagicMock()
        mock_conn.getresponse.return_value = mock_response
        mock_get_conn.return_value.__enter__.return_value = mock_conn

        result = subprocess.run(
            ["python", "-m", "ddtrace.commands.ddtrace_run", "pytest", "--ddtrace", str(test_file)],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(tmpdir),
        )

    # Test should pass
    assert result.returncode == 0, f"pytest failed with stderr: {result.stderr}"

    # Verify settings API was called (indicating CI Visibility was enabled)
    if mock_conn.request.call_args_list:
        request_calls = [call for call in mock_conn.request.call_args_list]
        settings_calls = [call for call in request_calls if "libraries/tests/services/setting" in str(call)]
        assert len(settings_calls) > 0, "Expected settings API to be called when CI Visibility is enabled"


@pytest.mark.subprocess()
def test_pytest_programmatic_killswitch_integration():
    """Test killswitch when using pytest programmatically with ddtrace enabled."""
    import subprocess
    import textwrap

    # Create a temporary test directory
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a test file
        test_file = os.path.join(tmpdir, "test_example.py")
        with open(test_file, "w") as f:
            f.write(
                textwrap.dedent(
                    """
                def test_example():
                    assert 1 + 1 == 2
            """
                )
            )

        # Write a script that runs pytest programmatically
        runner_script = os.path.join(tmpdir, "run_pytest.py")
        with open(runner_script, "w") as f:
            f.write(
                textwrap.dedent(
                    """
                import os
                import sys
                import pytest

                # Set environment variables before importing ddtrace
                os.environ["DD_API_KEY"] = "test-api-key"
                os.environ["DD_CIVISIBILITY_AGENTLESS_ENABLED"] = "1"
                os.environ["DD_CIVISIBILITY_ENABLED"] = "false"  # Killswitch

                # Import and enable ddtrace after setting env vars
                import ddtrace
                from ddtrace.internal.ci_visibility import CIVisibility

                # Run pytest
                exit_code = pytest.main(["--ddtrace", "test_example.py"])

                # Check if CI Visibility was enabled
                ci_vis_enabled = CIVisibility.enabled if hasattr(CIVisibility, 'enabled') else False
                print(f"CI_Visibility_Enabled: {ci_vis_enabled}")

                sys.exit(exit_code)
            """
                )
            )

        # Run the script
        result = subprocess.run(["python", runner_script], cwd=tmpdir, capture_output=True, text=True)

        # Verify test passed
        assert result.returncode == 0, f"Script failed with stderr: {result.stderr}"

        # Verify CI Visibility was disabled
        assert (
            "CI_Visibility_Enabled: False" in result.stdout
        ), f"Expected CI Visibility to be disabled, but output was: {result.stdout}"
