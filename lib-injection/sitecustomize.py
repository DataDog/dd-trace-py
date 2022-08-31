"""
This module when included on the PYTHONPATH will install the ddtrace package from pypi
for the Python runtime being used.
"""
import os
import sys


def _configure_ddtrace():
    # This import has the same effect as ddtrace-run for the current process.
    import ddtrace.bootstrap.sitecustomize

    bootstrap_dir = os.path.abspath(os.path.dirname(ddtrace.bootstrap.sitecustomize.__file__))
    prev_python_path = os.getenv("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = "%s%s%s" % (bootstrap_dir, os.path.pathsep, prev_python_path)

    # Also insert the bootstrap dir in the path of the current python process.
    sys.path.insert(0, bootstrap_dir)
    print("datadog autoinstrumentation: successfully configured python package")


# Avoid infinite loop when attempting to install ddtrace. This flag is set when
# the subprocess is launched to perform the install.
if "DDTRACE_PYTHON_INSTALL_IN_PROGRESS" not in os.environ:
    try:
        import ddtrace  # noqa: F401

    except ImportError:
        import subprocess

        print("datadog autoinstrumentation: installing python package")

        # Set the flag to avoid an infinite loop.
        env = os.environ.copy()
        env["DDTRACE_PYTHON_INSTALL_IN_PROGRESS"] = "true"

        # Execute the installation with the current interpreter
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "ddtrace"], env=env)
        except Exception:
            print("datadog autoinstrumentation: failed to install python package")
        else:
            print("datadog autoinstrumentation: successfully installed python package")
            _configure_ddtrace()
    else:
        print("datadog autoinstrumentation: ddtrace already installed, skipping install")
        _configure_ddtrace()
