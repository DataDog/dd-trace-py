import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

import pytest

from ddtrace._version import __version__ as host_ddtrace_version
from ddtrace._version import version_tuple as host_ddtrace_version_tuple


LIBS_INJECTION_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../lib-injection"))
LIBS_INJECTION_SRC_DIR = os.path.join(LIBS_INJECTION_DIR, "sources")
DL_WHEELS_SCRIPT = os.path.join(LIBS_INJECTION_DIR, "dl_wheels.py")
TEST_SUPPORT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "sitecustomize_test_support"))


def get_platform_details():
    python_version = platform.python_version()
    py_major_minor = ".".join(python_version.split(".")[:2])
    arch = platform.machine()

    libc, _ = platform.libc_ver()
    if sys.platform == "darwin":
        # dl_wheels doesn't support macos well, default to a linux target for test structure
        platform_tag = "manylinux2014"
    elif libc == "glibc":
        platform_tag = "manylinux2014"
    else:
        platform_tag = "musllinux_1_2"

    abi = f"cp{py_major_minor.replace('.', '')}"
    # Heuristic: Add 'm' for CPython 3.7 and older.
    if py_major_minor in ["3.7", "3.6", "3.5", "2.7"] and platform.python_implementation() == "CPython":
        abi += "m"

    return py_major_minor, arch, platform_tag, abi


@pytest.fixture(scope="session")
def ddtrace_injection_artifact():
    """
    Session-scoped fixture to prepare the ddtrace injection artifact
    (sources + unpacked wheels) once per session.
    """
    session_tmpdir = tempfile.mkdtemp(prefix="dd_artifact_session_")

    sources_dir_in_session_tmp = os.path.join(session_tmpdir, "sources")
    ddtrace_pkgs_output_dir = os.path.join(sources_dir_in_session_tmp, "ddtrace_pkgs")

    try:
        shutil.copytree(LIBS_INJECTION_SRC_DIR, sources_dir_in_session_tmp)

        version_file_path = os.path.join(sources_dir_in_session_tmp, "version")
        try:
            with open(version_file_path, "w") as f:
                f.write(host_ddtrace_version)
        except OSError as e:
            pytest.fail(f"[Session Setup] Failed to write version file {version_file_path}: {e}")

        version_to_download = None
        if (
            len(host_ddtrace_version_tuple) >= 2
            and isinstance(host_ddtrace_version_tuple[0], int)
            and isinstance(host_ddtrace_version_tuple[1], int)
        ):
            major, minor = host_ddtrace_version_tuple[:2]
            if minor > 0:
                version_to_download = f"{major}.{minor - 1}.0"
            elif major > 0:
                version_to_download = "3.6.0"
        if not version_to_download:
            pytest.fail(
                f"[Session Setup] Could not determine previous minor release version from tuple: {host_ddtrace_version_tuple}"
            )

        py_major_minor, arch, platform_tag, abi = get_platform_details()

        # Run dl_wheels.py
        dl_wheels_cmd = [
            sys.executable,
            DL_WHEELS_SCRIPT,
            "--python-version",
            py_major_minor,
            "--arch",
            arch,
            "--platform",
            platform_tag,
            "--ddtrace-version",
            version_to_download,
            "--output-dir",
            ddtrace_pkgs_output_dir,
        ]
        try:
            subprocess.run(dl_wheels_cmd, timeout=600, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            if "FileNotFoundError" in e.stderr and "unzip" in e.stderr:
                pass  # Expected failure, continue to manual unpack
            else:
                pytest.fail(
                    f"[Session Setup] dl_wheels.py failed unexpectedly:\nSTDOUT:\n{e.stdout}\nSTDERR:\n{e.stderr}"
                )
        except subprocess.TimeoutExpired as e:
            pytest.fail(
                f"[Session Setup] dl_wheels.py timed out:\nSTDOUT:\n{e.stdout or '[timeout]'}\nSTDERR:\n{e.stderr or '[timeout]'}"
            )

        # Manually unpack wheels
        target_site_packages_name = f"site-packages-ddtrace-py{py_major_minor}-{platform_tag}"
        target_site_packages_path = os.path.join(ddtrace_pkgs_output_dir, target_site_packages_name)
        os.makedirs(target_site_packages_path, exist_ok=True)

        if not os.path.isdir(ddtrace_pkgs_output_dir):
            print(
                f"[Session Setup] Warning: dl_wheels output directory {ddtrace_pkgs_output_dir} not found. Cannot unpack."
            )
            downloaded_wheels = []
        else:
            downloaded_wheels = [f for f in os.listdir(ddtrace_pkgs_output_dir) if f.endswith(".whl")]

        if not downloaded_wheels and os.path.isdir(ddtrace_pkgs_output_dir):
            print(f"[Session Setup] Warning: No wheels found in {ddtrace_pkgs_output_dir}.")

        for whl in downloaded_wheels:
            wheel_file_path = os.path.join(ddtrace_pkgs_output_dir, whl)
            try:
                with zipfile.ZipFile(wheel_file_path, "r") as zip_ref:
                    zip_ref.extractall(target_site_packages_path)
            except Exception as e:
                print(f"[Session Setup] Error unpacking {wheel_file_path}: {e}")
            finally:
                try:
                    os.remove(wheel_file_path)
                except OSError as e:
                    print(f"[Session Setup] Error removing wheel file {wheel_file_path}: {e}")

        # Protobuf cleanup
        sitepackages_root = Path(target_site_packages_path)
        directories_to_remove = [
            sitepackages_root / "google" / "protobuf",
            sitepackages_root / "google" / "_upb",
        ]
        directories_to_remove.extend(sitepackages_root.glob("protobuf-*.dist-info"))
        for directory in directories_to_remove:
            if directory.exists():
                try:
                    shutil.rmtree(directory)
                except Exception as e:
                    print(f"[Session Setup] Error removing {directory}: {e}")

        yield sources_dir_in_session_tmp

    except Exception as e:
        print(f"[Session Setup] Error preparing artifact: {e}")
        shutil.rmtree(session_tmpdir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(session_tmpdir, ignore_errors=True)


@pytest.fixture(scope="function")
def test_venv(ddtrace_injection_artifact):
    """
    Function-scoped fixture to create a clean venv for a test case and
    install specific packages.
    Yields: (path_to_python_executable, path_to_prepared_sources_dir)
    """
    prepared_sources_dir = ddtrace_injection_artifact
    venv_dir = tempfile.mkdtemp(prefix="ddtrace_test_venv_")
    temp_dirs_to_clean = [venv_dir]

    @pytest.fixture(autouse=True)
    def cleanup():
        yield
        for path in temp_dirs_to_clean:
            shutil.rmtree(path, ignore_errors=True)

    def _create_test_venv(packages_to_install=None):
        try:
            subprocess.check_call([sys.executable, "-m", "venv", venv_dir], timeout=60)
            python_executable = os.path.join(venv_dir, "bin", "python")
            pip_executable = os.path.join(venv_dir, "bin", "pip")

            if packages_to_install:
                for package, version in packages_to_install.items():
                    spec = f"{package}=={version}" if version else package
                    try:
                        subprocess.check_call(
                            [pip_executable, "install", spec],
                            timeout=120,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        )
                    except subprocess.CalledProcessError as e:
                        print(f"Failed to install {spec} in {venv_dir}: {e.stderr or e.stdout or e}")
                        raise

            return python_executable, prepared_sources_dir

        except Exception as e:
            print(f"Error creating test venv {venv_dir}: {e}")
            raise

    return _create_test_venv
