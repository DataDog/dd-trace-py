"""
This module when included on the PYTHONPATH will install the ddtrace package
from the locally available wheels that are included in the image.
"""
import os
import sys


def _configure_ddtrace():
    # This import has the same effect as ddtrace-run for the current process.
    import ddtrace.bootstrap.sitecustomize

    bootstrap_dir = os.path.abspath(os.path.dirname(ddtrace.bootstrap.sitecustomize.__file__))
    prev_python_path = os.getenv("PYTHONPATH", "")
    new_python_path = "%s%s%s" % (bootstrap_dir, os.path.pathsep, prev_python_path)
    os.environ["PYTHONPATH"] = new_python_path

    # Also insert the bootstrap dir in the path of the current python process.
    sys.path.insert(0, bootstrap_dir)
    print("[info] datadog autoinstrumentation: successfully configured python package, python path is %r" % new_python_path)


# Avoid infinite loop when attempting to install ddtrace. This flag is set when
# the subprocess is launched to perform the installation.
if "DDTRACE_PYTHON_INSTALL_IN_PROGRESS" not in os.environ:

    # First try to import ddtrace from the current PYTHONPATH
    # This should always fail as we don't expect the user to have already installed ddtrace.
    try:
        import ddtrace  # noqa: F401

    except ModuleNotFoundError:
        script_dir = os.path.dirname(__file__)
        pkgs_path = os.path.join(script_dir, "ddtrace_pkgs")
        site_pkgs_path = os.path.join(script_dir, "site-packages")

        # Now see if ddtrace has been installed already by us.
        # If it has we can skip installing and just configure.
        # Otherwise attempt to install the package.

        # Add the custom site-packages directory to the Python path to load the ddtrace package.
        sys.path.insert(0, site_pkgs_path)

        try:
            import ddtrace  # noqa: F401

        except ModuleNotFoundError:
            # The package needs to be installed.
            import subprocess

            print("[info] datadog autoinstrumentation: installing python package")

            # Copy the env, including any existing PYTHONPATH.
            env = os.environ.copy()

            # Set the flag to avoid an infinite loop when calling pip install (invokes another python process).
            env["DDTRACE_PYTHON_INSTALL_IN_PROGRESS"] = "true"

            # Get pip to use a tmp directory in the volume mount to avoid cross-device link errors.
            env["TMPDIR"] = os.path.join(script_dir, "tmp")
            print(env["TMPDIR"])

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
                        # Install to our custom site-packages.
                        "--target",
                        site_pkgs_path,
                    ],
                    env=env,
                    check=True,
                )
            except BaseException as e:
                print("[error] datadog autoinstrumentation: failed to install python package %s" % str(e))
            else:
                print("[info] datadog autoinstrumentation: successfully installed python package")
                _configure_ddtrace()
        else:
            print("[info] datadog autoinstrumentation: detected previous installation")
            _configure_ddtrace()
    except BaseException as e:
        print("[error] datadog autoinstrumentation: failed to import ddtrace python package %s" % str(e))
    else:
        print("[warning] datadog autoinstrumentation: ddtrace installed by user")
        _configure_ddtrace()
