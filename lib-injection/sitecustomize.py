"""
When included on the PYTHONPATH this module will initialize the PYTHONPATH to
include the directory containing the ddtrace package and its dependencies. It
then imports the ddtrace.bootstrap.sitecustomize module to automatically
instrument the application.
"""
import os
import sys


def _add_to_pythonpath(path):
    # type: (str) -> None
    """Adds a path to the start of PYTHONPATH."""
    prev_python_path = os.getenv("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = "%s%s%s" % (path, os.pathsep, prev_python_path)
    sys.path.insert(0, path)


pkgs_path = os.path.join(os.path.dirname(__file__), "ddtrace_pkgs")
bootstrap_dir = os.path.join(pkgs_path, "ddtrace", "bootstrap")
_add_to_pythonpath(pkgs_path)
_add_to_pythonpath(bootstrap_dir)

try:
    import ddtrace.bootstrap.sitecustomize  # noqa: F401
except BaseException as e:
    print("datadog autoinstrumentation: ddtrace failed to install:\n %s" % str(e))
else:
    print("datadog autoinstrumentation: ddtrace successfully installed")
