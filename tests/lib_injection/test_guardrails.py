import os
from pathlib import Path
import re
import subprocess

import pytest

from tests.contrib.integration_registry.test_contrib_versions import _get_integration_supported_versions


internal_contrib_dir = Path(os.path.dirname(__file__)) / ".." / ".." / "ddtrace" / "contrib" / "internal"

anthropic_spec = _get_integration_supported_versions(internal_contrib_dir, "anthropic")["anthropic"]
pymemcache_spec = _get_integration_supported_versions(internal_contrib_dir, "pymemcache")["pymemcache"]
pymongo_spec = _get_integration_supported_versions(internal_contrib_dir, "pymongo")["pymongo"]


def script_to_run(import_line):
    return """
try:
    # idk why ddtrace.auto is not working, but even so lib-injection should be calling patch, no idea whats going on.
    # so we're just calling patch_all manually here.

    from ddtrace import patch_all
    patch_all()

    %s

    print('successfully loaded ddtrace')
except ImportError:
    print("ddtrace not found")
    exit(1)
""" % (
        import_line
    )


TEST_CASES = [
    pytest.param(
        {"anthropic": "0.27.0"},
        [{"name": "anthropic", "spec": anthropic_spec}],
        id="anthropic_incompatible",
    ),
    pytest.param({"anthropic": "0.28.0"}, [], id="anthropic_compatible"),
    pytest.param(
        {"pymongo": "3.7.0", "bottle": "0.12.25"},
        [{"name": "pymongo", "spec": pymongo_spec}],
        id="mixed_pymongo_incomp_bottle_comp",
    ),
    pytest.param({"pymongo": "4.0.0", "bottle": "0.12.25"}, [], id="mixed_pymongo_bottle_both_comp"),
    pytest.param(
        {"anthropic": "0.27.0", "pymemcache": "3.3.0", "pymongo": "4.0.0"},
        [{"name": "anthropic", "spec": anthropic_spec}, {"name": "pymemcache", "spec": pymemcache_spec}],
        id="mixed_multi_incomp_one_comp",
    ),
    pytest.param({}, [], id="no_relevant_packages"),
    pytest.param({"click": "0.1"}, [], id="unrelated_package"),
]


@pytest.mark.parametrize("packages_to_install, expected_disabled_integrations", TEST_CASES)
def test_integration_compatibility_guardrail(test_venv, packages_to_install, expected_disabled_integrations):
    """
    Tests that sitecustomize correctly disables integrations based on installed package versions.
    It runs python -c '...' in a venv with specific packages installed and checks debug logs.
    """
    # The test_venv fixture returns the venv factory function
    venv_factory_func = test_venv
    # Call the factory to get the executable, sources dir, and base venv environment
    python_executable, sitecustomize_dir, base_env, venv_dir = venv_factory_func(packages_to_install)

    # Environment for the subprocess - start with venv base, then add injection path
    env = base_env.copy()
    # Prepend sitecustomize dir to existing venv site-packages PYTHONPATH from base_env
    venv_pythonpath = base_env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{sitecustomize_dir}{os.pathsep}{venv_pythonpath}"

    env["DD_TRACE_DEBUG"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"

    try:
        import_line = ""
        for package_name in packages_to_install.keys():
            import_line += f"import {package_name}\n    "

        result = subprocess.run(
            [python_executable, "-c", script_to_run(import_line)],
            capture_output=True,
            text=True,
            env=env,
            cwd=venv_dir,
            check=True,
            timeout=180,
        )
        stderr = result.stderr
        stdout = result.stdout

        # Check that ddtrace was loaded by sitecustomize
        assert "successfully loaded ddtrace" in stdout, f"ddtrace was not loaded properly. stdout: {stdout}"

        # Check that expected integrations are disabled
        for integration_info in expected_disabled_integrations:
            integration_name = integration_info["name"]
            supported_spec = integration_info["spec"]
            installed_version = packages_to_install[integration_name]

            expected_log = (
                f"Skipped patching '{integration_name}' integration, installed version: {installed_version} "
                f"is not compatible with integration support spec: {supported_spec}."
            )
            assert expected_log in stderr, (
                f"Expected to find log message for disabled integration '{integration_name}', but it was not found. "
                f"stderr: {stderr}"
            )

        # Check for any unexpectedly disabled integrations
        all_disabled_integrations = set(re.findall(r"Skipped patching '([^']*)' integration", stderr))

        expected_disabled_names = {d["name"] for d in expected_disabled_integrations}
        unexpectedly_disabled = all_disabled_integrations - expected_disabled_names
        assert (
            not unexpectedly_disabled
        ), f"Found unexpected disabled integrations: {unexpectedly_disabled}. stderr: {stderr}"

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed:\\nExit Code: {e.returncode}\\nstdout:\\n{e.stdout}\\nstderr:\\n{e.stderr}")
    except subprocess.TimeoutExpired as e:
        pytest.fail(f"Subprocess timed out:\\nstdout:\\n{e.stdout}\\nstderr:\\n{e.stderr}")
