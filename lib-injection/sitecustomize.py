"""
This module when included on the PYTHONPATH will install the ddtrace package from pypi
for the Python runtime being used.
"""
import os
import sys


# This special string is to be replaced at container build time so that the
# version is fixed in the source.
version = "<DD_TRACE_VERSION_TO_BE_REPLACED>"


def _configure_ddtrace():
    import ddtrace.internal.uwsgi as dduwsgi

    try:
        dduuwsgi.check_uwsgi()
    except dduwsgi.uWSGIConfigError:
        print(
            "datadog autoinstrumentation: skipping instrumentation due to incompatible uwsgi configuration. Please see https://ddtrace.readthedocs.io/en/stable/advanced_usage.html."
        )
        return
    except dduwsgi.uWSGIMasterProcess:
        pass

    # This import has the same effect as ddtrace-run for the current process.
    import ddtrace.bootstrap.sitecustomize

    bootstrap_dir = os.path.abspath(os.path.dirname(ddtrace.bootstrap.sitecustomize.__file__))
    prev_python_path = os.getenv("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = "%s%s%s" % (bootstrap_dir, os.path.pathsep, prev_python_path)

    # Also insert the bootstrap dir in the path of the current python process.
    sys.path.insert(0, bootstrap_dir)
    print("datadog autoinstrumentation: successfully configured python package")


# Avoid infinite loop when attempting to install ddtrace. This flag is set when
# the subprocess is launched to perform the installation.
if "DDTRACE_PYTHON_INSTALL_IN_PROGRESS" not in os.environ:
    try:
        import ddtrace  # noqa: F401

    except ImportError:
        import subprocess

        print("datadog autoinstrumentation: installing python package")

        # Set the flag to avoid an infinite loop.
        env = os.environ.copy()
        env["DDTRACE_PYTHON_INSTALL_IN_PROGRESS"] = "true"

        if "git" in version:
            ddtrace_version = version
        else:
            ddtrace_version = "ddtrace==%s" % version

        # When uwsgi is being used, sys.executable is `/usr/local/bin/uwsgi`
        # (or wherever uwsgi is installed to) as it programatically invokes the
        # CPython interpreter.
        # In this case, assume that the version of Python uwsgi uses is the one
        # on the path (eg. `which python`).
        executable = sys.executable
        if "uwsgi" in executable:
            executable = os.which("python")
        try:
            subprocess.run([executable, "-m", "pip", "install", ddtrace_version], env=env, check=True)
        except Exception:
            print("datadog autoinstrumentation: failed to install python package version %r" % ddtrace_version)
        else:
            print("datadog autoinstrumentation: successfully installed python package version %r" % ddtrace_version)
            _configure_ddtrace()
    else:
        print("datadog autoinstrumentation: ddtrace already installed, skipping install")
        _configure_ddtrace()
