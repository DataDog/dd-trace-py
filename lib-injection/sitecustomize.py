import os
import sys


def _configure_ddtrace():
    # This import has the same effect as ddtrace-run for the current
    # process.
    import ddtrace.bootstrap.sitecustomize

    bootstrap_dir = os.path.abspath(os.path.dirname(ddtrace.bootstrap.sitecustomize.__file__))
    prev_python_path = os.getenv("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = "%s%s%s" % (bootstrap_dir, os.path.pathsep, prev_python_path)

    # Also insert the bootstrap dir in the path of the current python process.
    sys.path.insert(0, bootstrap_dir)
    print("datadog autoinstrumentation: successfully configured python package")


if "DD_INSTALL_DDTRACE_PYTHON" in os.environ:
    try:
        import ddtrace

    except ImportError:
        import subprocess

        print("datadog autoinstrumentation: installing python package")

        # Avoid infinite recursion
        del os.environ["DD_INSTALL_DDTRACE_PYTHON"]

        # Execute the installation with the current interpreter
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "ddtrace"], env=os.environ.copy())
        except:
            print("datadog autoinstrumentation: failed to install python package")
        else:
            print("datadog autoinstrumentation: successfully installed python package")
            _configure_ddtrace()
    else:
        print("datadog autoinstrumentation: ddtrace already installed, skipping install")
        _configure_ddtrace()
