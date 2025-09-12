import os
import platform
import shutil
import subprocess
import sys
import tempfile


try:
    import tomllib
except ImportError:
    import tomli as tomllib

import pytest

from ddtrace._version import __version__ as host_ddtrace_version


LIBS_INJECTION_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../lib-injection"))
LIBS_INJECTION_SRC_DIR = os.path.join(LIBS_INJECTION_DIR, "sources")
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def _get_ddtrace_core_dependencies():
    """Reads core dependencies from pyproject.toml."""
    with open(os.path.join(PROJECT_ROOT, "pyproject.toml"), "rb") as f:
        pyproject = tomllib.load(f)
    return pyproject["project"]["dependencies"]


DDTRACE_CORE_DEPENDENCIES = _get_ddtrace_core_dependencies()


def get_platform_details():
    """Determines platform details needed for constructing the site-packages directory name."""
    python_version = platform.python_version()
    py_major_minor = ".".join(python_version.split(".")[:2])

    return py_major_minor, "manylinux2014"


@pytest.fixture(scope="session")
def ddtrace_injection_artifact():
    """
    Session-scoped fixture to prepare the injection artifact:
    1. Copies injection source files (sitecustomize.py, etc.) into a temporary directory.
    2. Copies the host's ddtrace package source into the expected `ddtrace_pkgs/site-packages...` structure.
    3. Installs build dependencies necessary for setup.py to run.
    4. Generates package metadata (.egg-info) for entry point discovery using setup.py.
    5. Writes the host's ddtrace version to the `version` file.
    Yields: path_to_prepared_sources_dir
    """
    session_tmpdir = tempfile.mkdtemp(prefix="dd_injection_artifact_session_")
    sources_dir_in_session_tmp = os.path.join(session_tmpdir, "sources")

    try:
        # 1. Copy source files needed by lib-injection
        shutil.copytree(
            LIBS_INJECTION_SRC_DIR, sources_dir_in_session_tmp, ignore=shutil.ignore_patterns("ddtrace_pkgs")
        )

        # 2. Create the target site-packages directory structure
        py_major_minor, platform_tag = get_platform_details()
        target_site_packages_name = f"site-packages-ddtrace-py{py_major_minor}-{platform_tag}"
        target_site_packages_path = os.path.join(sources_dir_in_session_tmp, "ddtrace_pkgs", target_site_packages_name)
        os.makedirs(target_site_packages_path, exist_ok=True)

        # 3. Copy the ddtrace source code into our temp site-packages
        host_ddtrace_path = os.path.join(PROJECT_ROOT, "ddtrace")
        target_ddtrace_dir = os.path.join(target_site_packages_path, "ddtrace")
        shutil.copytree(host_ddtrace_path, target_ddtrace_dir, symlinks=True)

        # 4. Install build dependencies necessary for setup.py to run.
        with open(os.path.join(PROJECT_ROOT, "pyproject.toml"), "rb") as f:
            pyproject = tomllib.load(f)
        build_requires = pyproject.get("build-system", {}).get("requires", [])
        if build_requires:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install"] + build_requires,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                text=True,
                timeout=180,
            )

        setup_py_path = os.path.join(PROJECT_ROOT, "setup.py")
        if not os.path.exists(setup_py_path):
            pytest.fail(f"setup.py not found at {setup_py_path}. This test requires it to generate package metadata.")

        # 5. Generate the .egg-info metadata directory right into our site-packages.
        subprocess.check_call(
            [sys.executable, "setup.py", "egg_info", f"--egg-base={target_site_packages_path}"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            text=True,
            timeout=120,
        )

        # 5. Write the ddtrace version file
        version_file_path = os.path.join(sources_dir_in_session_tmp, "version")
        with open(version_file_path, "w") as f:
            f.write(host_ddtrace_version)

        yield sources_dir_in_session_tmp

    finally:
        shutil.rmtree(session_tmpdir, ignore_errors=True)


@pytest.fixture(scope="function")
def test_venv(ddtrace_injection_artifact):
    """
    Function-scoped fixture factory to create a clean venv for a test case,
    install core ddtrace dependencies (from hardcoded list, excluding ddtrace itself),
    install any other specified packages,
    and provide access to the prepared injection artifact.
    Yields a factory function that takes `packages_to_install` dict.
    The factory function returns: (path_to_python_executable, path_to_prepared_sources_dir, base_env)
    """
    prepared_sources_dir = ddtrace_injection_artifact
    venvs_to_clean = []

    def _create_test_venv(packages_to_install=None):
        venv_dir = tempfile.mkdtemp(prefix="dd_test_venv_")
        venvs_to_clean.append(venv_dir)

        try:
            subprocess.check_call([sys.executable, "-m", "venv", venv_dir], timeout=60)
            python_executable = os.path.join(venv_dir, "bin", "python")
            pip_executable = os.path.join(venv_dir, "bin", "pip")

            # Construct the base environment needed to run things in this venv
            base_env = {
                **os.environ,
                "VIRTUAL_ENV": venv_dir,
                "PATH": os.path.dirname(pip_executable) + os.pathsep + os.environ.get("PATH", ""),
                "PYTHONPATH": "",
            }

            if DDTRACE_CORE_DEPENDENCIES:
                core_install_cmd = [pip_executable, "install", "--no-cache-dir"] + DDTRACE_CORE_DEPENDENCIES
                subprocess.run(
                    core_install_cmd,
                    timeout=300,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=base_env,
                )

            # Install test-specific packages
            if packages_to_install:
                install_specs = []
                for package, version in packages_to_install.items():
                    spec = f"{package}=={version}" if version else package
                    install_specs.append(spec)

                if install_specs:
                    test_install_cmd = [pip_executable, "install", "--no-cache-dir"] + install_specs
                    subprocess.run(
                        test_install_cmd,
                        timeout=300,
                        check=True,
                        capture_output=True,
                        text=True,
                        env=base_env,
                    )

            return python_executable, prepared_sources_dir, base_env, venv_dir

        except Exception as e:
            pytest.fail(f"Failed to create or setup test venv {venv_dir}: {e}")

    yield _create_test_venv

    for venv_path in venvs_to_clean:
        shutil.rmtree(venv_path, ignore_errors=True)


@pytest.fixture
def mock_telemetry_forwarder(tmp_path):
    def _setup(env, python_executable):
        telemetry_output_file = tmp_path / "telemetry.json"
        forwarder_script_file = tmp_path / "forwarder.sh"
        # Create a mock forwarder shell script. This script will be executed by sitecustomize.py
        # and will write the telemetry payload to a file that we can inspect.
        forwarder_script_file.write_text(
            f"""#!/bin/sh
cat > "{telemetry_output_file}"
"""
        )
        os.chmod(forwarder_script_file, 0o755)

        env["DD_TELEMETRY_FORWARDER_PATH"] = str(forwarder_script_file)
        return telemetry_output_file

    return _setup
