import os

if "DD_INSTALL_DDTRACE_PYTHON" in os.environ:
    try:
        import ddtrace

    except ImportError:
        import subprocess
        import sys

        print("datadog autoinstrumentation: installing python package")
        # Avoid infinite recursion
        del os.environ["DD_INSTALL_DDTRACE_PYTHON"]

        # Execute the install with the current interpreter
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "ddtrace"], env=os.environ.copy())
        except:
            print("datdog autoinstrumentation: failed to install python package")
        else:
            # This import has the same effect as ddtrace-run for the current
            # process.
            import ddtrace.bootstrap.sitecustomize

            bootstrap_dir = os.path.abspath(os.path.dirname(ddtrace.bootstrap.sitecustomize.__file__))
            prev_python_path = os.getenv("PYTHONPATH", "")
            os.environ["PYTHONPATH"] = "%s%s%s" % (bootstrap_dir, os.path.pathsep, prev_python_path)
            print("datadog autoinstrumentation: successfully installed python package")
    else:
        print("datadog autoinstrumentation: ddtrace already installed, skipping")
