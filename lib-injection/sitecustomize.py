"""
This module when included on the PYTHONPATH will install the ddtrace package
from the locally available wheels that are included in the image.
"""
import os
import sys


script_dir = os.path.dirname(__file__)
pkgs_path = os.path.join(script_dir, "ddtrace_pkgs")
site_pkgs_path = os.path.join(script_dir, "site-packages")


def _log(msg, *args, level="info"):
    """Log a message to stderr.

    This function is provided instead of built-in Python logging since we can't rely on any logger
    being configured.
    """
    print("%s:datadog.autoinstrumentation(pid: %d): " % (level.upper(), os.getpid()) + msg % args, file=sys.stderr)


def _configure_ddtrace():
    # This import has the same effect as ddtrace-run for the current process.
    import ddtrace.bootstrap.sitecustomize

    # Modify the PYTHONPATH for any subprocesses:
    #   - Remove the PYTHONPATH entry used to bootstrap this installation as it's no longer necessary
    #     now that the package is installed.
    #   - Add the custom site-packages directory to PYTHONPATH to ensure the ddtrace package can be loaded
    #   - Add the ddtrace bootstrap dir to the PYTHONPATH to achieve the same effect as ddtrace-run.

    python_path = os.getenv("PYTHONPATH", "").split(os.pathsep)
    if script_dir in python_path:
        python_path.remove(script_dir)
    python_path.insert(0, site_pkgs_path)
    bootstrap_dir = os.path.abspath(os.path.dirname(ddtrace.bootstrap.sitecustomize.__file__))
    python_path.insert(0, bootstrap_dir)
    python_path = os.pathsep.join(python_path)
    os.environ["PYTHONPATH"] = python_path

    # Also insert the bootstrap dir in the path of the current python process.
    sys.path.insert(0, bootstrap_dir)
    _log("successfully configured ddtrace package, python path is %r" % os.environ["PYTHONPATH"])


# Avoid infinite loop when attempting to install ddtrace. This flag is set when
# the subprocess is launched to perform the installation.
if "DDTRACE_PYTHON_INSTALL_IN_PROGRESS" not in os.environ:

    # First try to import ddtrace from the current PYTHONPATH
    # This should always fail as we don't expect the user to have already installed ddtrace.
    try:
        import ddtrace  # noqa: F401

    except ModuleNotFoundError:

        _log("ddtrace package not detected, attempting installation now")

        # Add the custom site-packages directory to the Python path to load the ddtrace package.
        sys.path.insert(0, site_pkgs_path)

        try:
            import ddtrace  # noqa: F401

        except ModuleNotFoundError:
            # The package needs to be installed.
            import subprocess

            # Invalidate the importer cache since the package will now be installed.
            if site_pkgs_path in sys.path_importer_cache:
                del sys.path_importer_cache[site_pkgs_path]

            _log("installing python package")

            # Copy the env, including any existing PYTHONPATH.
            env = os.environ.copy()

            # Avoid an infinite loop when calling pip install (invokes another python process).
            env["DDTRACE_PYTHON_INSTALL_IN_PROGRESS"] = "true"

            # Get pip to use a tmp directory in the volume mount to avoid cross-device link errors.
            env["TMPDIR"] = os.path.join(script_dir, "tmp")

            try:
                subprocess.run(
                    [
                        # Execute the installation with the current interpreter (should be the one used by the app).
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        # Disable the cache to save space and avoid permissions warnings when unprivileged users
                        # are being used.
                        "--no-cache-dir",
                        # Don't use the internet to look for packages.
                        "--no-index",
                        # Look for wheels at the path specified.
                        "--find-links",
                        pkgs_path,
                        "ddtrace",
                        # Install to our custom site-packages directory.
                        "--target",
                        site_pkgs_path,
                    ],
                    env=env,
                    check=True,
                )
            except BaseException as e:
                _log("failed to install python package %s", str(e), level="error")
            else:
                _log("successfully installed python package")
                _configure_ddtrace()
        else:
            _log("detected previous installation")
            _configure_ddtrace()
    except BaseException as e:
        _log("failed to import ddtrace python package %s" % str(e))
    else:
        _log("ddtrace installed by user, skipping install")
        _configure_ddtrace()
