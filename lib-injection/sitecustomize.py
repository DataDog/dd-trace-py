"""
This module when included on the PYTHONPATH will install the ddtrace package
from the locally available wheels that are included in the image.
"""
import os
import sys
import site

print(
    "lib injection started: %s, python: %s, site packages: %s, sys.path: %s"
    % (__file__, sys.executable, site.getsitepackages(), sys.path)
)


def _configure_ddtrace():
    # This import has the same effect as ddtrace-run for the current process.
    print("attempt to import ddtrace")
    import ddtrace.bootstrap.sitecustomize
    from ddtrace import __version__

    print("imported ddtrace successfully. ddtrace_version: {}".format(__version__))

    bootstrap_dir = os.path.abspath(os.path.dirname(ddtrace.bootstrap.sitecustomize.__file__))
    prev_python_path = os.getenv("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = "%s%s%s" % (bootstrap_dir, os.path.pathsep, prev_python_path)

    # Also insert the bootstrap dir in the path of the current python process.
    sys.path.insert(0, bootstrap_dir)
    print(
        "datadog autoinstrumentation: successfully configured python package. PythonPath: {}".format(
            os.environ["PYTHONPATH"]
        )
    )


# Avoid infinite loop when attempting to install ddtrace. This flag is set when
# the subprocess is launched to perform the installation.
if "DDTRACE_PYTHON_INSTALL_IN_PROGRESS" not in os.environ:
    try:
        import ddtrace  # noqa: F401

        print("ddtrace version in lib injection code: {}".format(ddtrace.__version__))

    except Exception:
        import subprocess

        print("datadog autoinstrumentation: installing python package")

        # Set the flag to avoid an infinite loop.
        env = os.environ.copy()
        env["DDTRACE_PYTHON_INSTALL_IN_PROGRESS"] = "true"

        # Execute the installation with the current interpreter
        try:
            pkgs_path = os.path.join(os.path.dirname(__file__), "ddtrace_pkgs")
            p = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-index", "--find-links", pkgs_path, "ddtrace"],
                env=env,
                capture_output=True,
                check=True,
            )
            print(
                "result of installing ddtrace: returncode={}  stdout={}  stderr{}".format(
                    p.returncode, p.stdout, p.stderr
                )
            )
            print("datadog autoinstrumentation: successfully installed python package")
        except BaseException as e:
            print("datadog autoinstrumentation: failed to install python package %s" % str(e))
        else:
            try:
                _configure_ddtrace()
            except Exception as e:
                print("failed to configure ddtrace: {}".format(str(e)))
    else:
        print("datadog autoinstrumentation: ddtrace already installed, skipping install")
        _configure_ddtrace()
else:
    print("NOP: lib injection is already in progress")
print(
    "lib injection complete: %s, python: %s, site packages: %s, sys.path: %s"
    % (__file__, sys.executable, site.getsitepackages(), sys.path)
)
