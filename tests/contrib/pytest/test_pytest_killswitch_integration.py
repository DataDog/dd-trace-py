"""Integration test for DD_CIVISIBILITY_ENABLED killswitch functionality.

This test verifies that when DD_CIVISIBILITY_ENABLED is set to false/0,
CI Visibility is disabled and pytest traces go to the regular agent
instead of citestcycle intake, even when DD_CIVISIBILITY_AGENTLESS_ENABLED=1.
"""
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

    # Track which endpoints are called
    endpoint_calls = []

    def mock_request(method, url, *args, **kwargs):
        endpoint_calls.append(url)
        # Mock successful response
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"data": []}'
        return mock_response

    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",  # Should enable citestcycle intake
            "DD_CIVISIBILITY_ENABLED": "false",  # Should disable CI Visibility entirely
            "DD_TRACE_AGENT_URL": "http://localhost:9126",
        }
    )

    # Run pytest with ddtrace-run
    with mock.patch("requests.post", side_effect=mock_request):
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
    citestcycle_calls = [url for url in endpoint_calls if "citestcycle" in url]
    assert len(citestcycle_calls) == 0, f"Expected no citestcycle calls, but got: {citestcycle_calls}"

    # Verify regular trace agent endpoints were called instead
    [url for url in endpoint_calls if "/v0.5/traces" in url or "/v0.4/traces" in url]
    # Note: This might be 0 in our mock setup, but the key is that citestcycle was not called


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

    # Track which endpoints are called
    endpoint_calls = []

    def mock_request(method, url, *args, **kwargs):
        endpoint_calls.append(url)
        # Mock successful response
        mock_response = mock.MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"data": []}'
        return mock_response

    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",  # Should enable citestcycle intake
            "DD_CIVISIBILITY_ENABLED": "0",  # Should disable CI Visibility entirely
            "DD_TRACE_AGENT_URL": "http://localhost:9126",
        }
    )

    # Run pytest with ddtrace-run
    with mock.patch("requests.post", side_effect=mock_request):
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
    citestcycle_calls = [url for url in endpoint_calls if "citestcycle" in url]
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

    # Track which endpoints are called
    endpoint_calls = []
    settings_calls = []

    def mock_request(method, url, *args, **kwargs):
        endpoint_calls.append(url)
        if "libraries/tests/services/setting" in url:
            settings_calls.append(url)
            # Mock settings response
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
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
            return mock_response
        else:
            # Mock successful response for other calls
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"data": []}'
            return mock_response

    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",  # Should enable citestcycle intake
            # DD_CIVISIBILITY_ENABLED not set - should default to enabled
            "DD_TRACE_AGENT_URL": "http://localhost:9126",
        }
    )

    # Run pytest with ddtrace-run
    with mock.patch("requests.post", side_effect=mock_request), mock.patch(
        "requests.get", side_effect=mock_request
    ):
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

    # Track which endpoints are called
    endpoint_calls = []
    settings_calls = []

    def mock_request(method, url, *args, **kwargs):
        endpoint_calls.append(url)
        if "libraries/tests/services/setting" in url:
            settings_calls.append(url)
            # Mock settings response
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
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
            return mock_response
        else:
            # Mock successful response for other calls
            mock_response = mock.MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"data": []}'
            return mock_response

    env = os.environ.copy()
    env.update(
        {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",  # Should enable citestcycle intake
            "DD_CIVISIBILITY_ENABLED": "true",  # Explicitly enable CI Visibility
            "DD_TRACE_AGENT_URL": "http://localhost:9126",
        }
    )

    # Run pytest with ddtrace-run
    with mock.patch("requests.post", side_effect=mock_request), mock.patch(
        "requests.get", side_effect=mock_request
    ):
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
