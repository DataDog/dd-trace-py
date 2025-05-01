import os
import platform
import shutil
import subprocess
import sys
import tempfile

import pytest

from ddtrace._version import __version__ as host_ddtrace_version


LIBS_INJECTION_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../lib-injection"))
LIBS_INJECTION_SRC_DIR = os.path.join(LIBS_INJECTION_DIR, "sources")
TEST_SUPPORT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "sitecustomize_test_support"))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))

# Core dependencies copied from pyproject.toml [project.dependencies]
DDTRACE_CORE_DEPENDENCIES = [
    "packaging",
    "protobuf>=3",
    "tenacity",
    "attrs",
    "wrapt>=1",
    "envier~=0.6.1",
    "opentelemetry-api>=1",
    "xmltodict>=0.12",
    "typing_extensions",
    "bytecode>=0.16.0", # ; python_version>='3.13.0'
    "bytecode>=0.15.1", # ; python_version~='3.12.0'
    "bytecode>=0.14.0", # ; python_version~='3.11.0'
    "bytecode>=0.13.0", # ; python_version<'3.11'
    "importlib_metadata<=6.5.0", # ; python_version<'3.8'
    "legacy-cgi>=2.0.0", # ; python_version>='3.13.0'
]


def get_platform_details():
    """Determines platform details needed for constructing the site-packages directory name."""
    python_version = platform.python_version()
    py_major_minor = ".".join(python_version.split(".")[:2])

    return py_major_minor, "manylinux2014"


@pytest.fixture(scope="session")
def ddtrace_injection_artifact():
    """
    Session-scoped fixture to prepare the injection artifact:
    1. Copies injection source files (sitecustomize.py, supported_integration_versions.csv, etc.).
    2. Copies the host's ddtrace package into the expected `ddtrace_pkgs/site-packages...` structure.
    3. Writes the host's ddtrace version to the `version` file.

    Yields: path_to_prepared_sources_dir
    """
    session_tmpdir = tempfile.mkdtemp(prefix="dd_injection_artifact_session_")
    sources_dir_in_session_tmp = os.path.join(session_tmpdir, "sources")

    try:
        # 1. Copy source files (sitecustomize.py, CSVs, etc.)
        shutil.copytree(LIBS_INJECTION_SRC_DIR, sources_dir_in_session_tmp,
                         ignore=shutil.ignore_patterns('ddtrace_pkgs'))

        # 3. Write the host's ddtrace version into the sources dir. Needed by sitecustomize.py
        version_file_path = os.path.join(sources_dir_in_session_tmp, "version")
        try:
            with open(version_file_path, "w") as f:
                f.write(host_ddtrace_version)
        except OSError as e:
            pytest.fail(f"[Session Setup] Failed to write version file {version_file_path}: {e}")

        # 2. Copy the host's ddtrace package details into the correct dir structure
        py_major_minor, platform_tag = get_platform_details()
        target_site_packages_name = f"site-packages-ddtrace-py{py_major_minor}-{platform_tag}"
        target_site_packages_path = os.path.join(sources_dir_in_session_tmp, "ddtrace_pkgs", target_site_packages_name)
        os.makedirs(target_site_packages_path, exist_ok=True)

        import ddtrace
        host_ddtrace_path = os.path.dirname(ddtrace.__file__)

        # Copy the host ddtrace package into the target structure
        target_ddtrace_dir = os.path.join(target_site_packages_path, 'ddtrace')
        try:
            if os.path.exists(target_ddtrace_dir):
                if os.path.islink(target_ddtrace_dir):
                    os.unlink(target_ddtrace_dir)
                elif os.path.isdir(target_ddtrace_dir):
                     shutil.rmtree(target_ddtrace_dir)
                else:
                    os.remove(target_ddtrace_dir)

            shutil.copytree(host_ddtrace_path, target_ddtrace_dir, symlinks=True)
        except Exception as e:
            pytest.fail(f"[Session Setup] Failed to copy host ddtrace from {host_ddtrace_path} to {target_ddtrace_dir}: {e}")

        yield sources_dir_in_session_tmp

    except Exception as e:
        shutil.rmtree(session_tmpdir, ignore_errors=True)
        pytest.fail(f"[Session Setup] Artifact preparation failed: {e}")
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

            # 1. Install core dependencies from the hardcoded list
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

            # 2. Install test-specific packages
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

            # Return the executables, sources, env, and the venv path itself
            return python_executable, prepared_sources_dir, base_env, venv_dir

        except Exception as e:
            pytest.fail(f"Failed to create or setup test venv {venv_dir}: {e}")

    yield _create_test_venv

    for venv_path in venvs_to_clean:
        shutil.rmtree(venv_path, ignore_errors=True)
