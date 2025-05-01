from tests.commands.test_runner import inject_sitecustomize
import subprocess
import os
import json
import pytest


SCRIPT_TO_RUN = """
import os
import json
import sys

try:
    # Print environment variables starting with DD_TRACE_ for inspection
    trace_vars = {k: v for k, v in os.environ.items() if k.startswith("DD_TRACE_")}

    # Also include _DD_INJECT_WAS_ATTEMPTED to confirm sitecustomize ran
    if "_DD_INJECT_WAS_ATTEMPTED" in os.environ:
        trace_vars["_DD_INJECT_WAS_ATTEMPTED"] = os.environ["_DD_INJECT_WAS_ATTEMPTED"]

    import ddtrace

    print(json.dumps(trace_vars))
except Exception as e:
    print(f"Lib injection script error: {e}")
    raise
"""

TEST_CASES = [
    pytest.param(
        {"redis": "3.5.3"}, # Below >=4.0.0
        {"DD_TRACE_REDIS_ENABLED": "false"},
        id="redis_incompatible"
    ),
    pytest.param(
        {"redis": "4.6.0"}, # At/Above >=4.0.0
        {},
        id="redis_compatible"
    ),
    pytest.param(
        {"requests": "0.2.0"}, # Below >=2.0.0
        {"DD_TRACE_REQUESTS_ENABLED": "false"},
        id="requests_incompatible"
    ),
    pytest.param(
        {"requests": "2.28.1"}, # At/Above >=2.0.0
        {},
        id="requests_compatible"
    ),
    pytest.param(
        {"flask": "0.12.5"}, # Below >=1.0.0
        {"DD_TRACE_FLASK_ENABLED": "false"},
        id="flask_incompatible"
    ),
    pytest.param(
        {"flask": "2.0.0"}, # At/Above >=1.0.0
        {},
        id="flask_compatible"
    ),
    pytest.param(
        {"pymysql": "0.9.0"}, # Old version, but >=0.0.0 supported
        {},
        id="pymysql_old_compatible"
    ),
    pytest.param(
        {"pymysql": "1.1.0"}, # Newer version, >=0.0.0 supported
        {},
        id="pymysql_new_compatible"
    ),
    pytest.param(
        {"redis": "3.5.3", "requests": "2.28.1"}, # redis incompatible, requests compatible
        {"DD_TRACE_REDIS_ENABLED": "false"},
        id="mixed_redis_incomp_req_comp"
    ),
    pytest.param(
        {"redis": "4.6.0", "requests": "0.2.0"}, # redis compatible, requests incompatible
        {"DD_TRACE_REQUESTS_ENABLED": "false"},
        id="mixed_redis_comp_req_incomp"
    ),
    pytest.param(
        {"flask": "0.12.5", "bottle": "0.12.25"}, # flask incompatible, bottle compatible
        {"DD_TRACE_FLASK_ENABLED": "false"},
        id="mixed_flask_incomp_bottle_comp"
    ),
    pytest.param(
        {"flask": "2.0.0", "bottle": "0.12.25"}, # both compatible
        {},
        id="mixed_flask_bottle_comp"
    ),
    pytest.param(
        {"redis":"3.5.3", "requests":"0.2.0", "flask":"2.0.0"}, # 2 incompatible, 1 compatible
        {"DD_TRACE_REDIS_ENABLED": "false", "DD_TRACE_REQUESTS_ENABLED": "false"},
        id="mixed_multi_incomp_one_comp"
    ),
    pytest.param(
        {}, # No relevant packages installed
        {},
        id="no_relevant_packages"
    ),
    pytest.param(
        {"click": "0.1"}, # Use a real package ('click') that we don't integrate with
        {},
        id="unrelated_package"
    ),
]

@pytest.mark.parametrize("packages_to_install, expected_disabled_vars", TEST_CASES)
def test_integration_compatibility_guardrail(test_venv, packages_to_install, expected_disabled_vars):
    """
    Tests that sitecustomize correctly disables integrations based on installed package versions.
    It runs python -c '...' in a venv with specific packages installed and checks env vars.
    """
    # The test_venv fixture returns the venv factory function
    venv_factory_func = test_venv 
    # Call the factory to get the executable and the path to the PREPARED sources dir
    python_executable, sitecustomize_dir = venv_factory_func(packages_to_install)

    # Environment for the subprocess
    env = os.environ.copy()

    env["PYTHONPATH"] = sitecustomize_dir
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:0"

    try:
        result = subprocess.run(
            [python_executable, "-c", SCRIPT_TO_RUN],
            capture_output=True,
            text=True,
            env=env,
            check=True, # Raise exception on non-zero exit code
            timeout=180 # Generous timeout for pip installs + script run
        )
        output_env_vars = json.loads(result.stdout)

        print("\n     Subprocess stdout:")
        print(f"              {result.stdout.strip()}")
        print("     Parsed Env Vars:")
        print(f"              {output_env_vars}")
        print("     Expected Disabled:")
        print(f"              {expected_disabled_vars}\n")

        assert output_env_vars.get("_DD_INJECT_WAS_ATTEMPTED") == "true", \
               f"sitecustomize did not set the _DD_INJECT_WAS_ATTEMPTED flag. stderr: {result.stderr}"

        for var_name, expected_value in expected_disabled_vars.items():
            assert output_env_vars.get(var_name) == expected_value, \
                   f"Expected env var '{var_name}' to be '{expected_value}', but got '{output_env_vars.get(var_name)}'. stderr: {result.stderr}"

        all_found_integration_flags = {k for k in output_env_vars if k.startswith("DD_TRACE_") and k.endswith("_ENABLED")}

        unexpectedly_disabled_flags = set()
        for flag in all_found_integration_flags:
            if flag not in expected_disabled_vars and output_env_vars.get(flag) == "false":
                unexpectedly_disabled_flags.add(flag)

        assert not unexpectedly_disabled_flags, \
               f"The following env vars were unexpectedly set to 'false': {unexpectedly_disabled_flags}. stderr: {result.stderr}"

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed:\nExit Code: {e.returncode}\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")
    except subprocess.TimeoutExpired as e:
         pytest.fail(f"Subprocess timed out:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")
    except json.JSONDecodeError as e:
        pytest.fail(f"Failed to decode JSON output from subprocess:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\nError: {e}")


# TODO: Add tests for DD_INJECT_FORCE=true overriding the disablement
# TODO: Add tests for runtime version incompatibility checks
# TODO: Add tests for executable deny list checks

