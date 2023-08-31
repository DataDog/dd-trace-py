"""
This module when included on the PYTHONPATH will update the PYTHONPATH to point to a directory
containing the ddtrace package compatible with the current Python version and platform.
"""
import os
import sys


debug_mode = os.environ.get("DD_TRACE_DEBUG", "").lower() in ("true", "1", "t")


def _get_clib():
    """Return the C library used by the system.

    If GNU is not detected then returns MUSL.
    """
    import platform

    libc, version = platform.libc_ver()
    if libc == "glibc":
        return "gnu"
    return "musl"


def _log(msg, *args, level="info"):
    """Log a message to stderr.

    This function is provided instead of built-in Python logging since we can't rely on any logger
    being configured.
    """
    if not debug_mode and level == "debug":
        return
    print("%s:datadog.autoinstrumentation(pid: %d): " % (level.upper(), os.getpid()) + msg % args, file=sys.stderr)


try:
    import ddtrace
except ModuleNotFoundError:
    _log("user-installed ddtrace not found, configuring application to use injection site-packages")

    platform = "manylinux2014" if _get_clib() == "gnu" else "musllinux_1_1"
    _log("detected platform %s" % platform, level="debug")

    script_dir = os.path.dirname(__file__)
    pkgs_path = os.path.join(script_dir, "ddtrace_pkgs")
    _log("ddtrace_pkgs path is %r" % pkgs_path, level="debug")
    _log("ddtrace_pkgs contents: %r" % os.listdir(pkgs_path), level="debug")

    python_version = ".".join(str(i) for i in sys.version_info[:2])
    site_pkgs_path = os.path.join(pkgs_path, "site-packages-ddtrace-py%s-%s" % (python_version, platform))
    _log("site-packages path is %r" % site_pkgs_path, level="debug")
    if not os.path.exists(site_pkgs_path):
        _log("ddtrace site-packages not found in %r" % site_pkgs_path, level="error")

    # Add the custom site-packages directory to the Python path to load the ddtrace package.
    sys.path.insert(0, site_pkgs_path)
    _log("sys.path %s" % sys.path, level="debug")

    try:
        import ddtrace  # noqa: F401

    except BaseException as e:
        _log("failed to load ddtrace module: %s" % e, level="error")
        raise
    else:
        # This import has the same effect as ddtrace-run for the current process (auto-instrument all libraries).
        import ddtrace.bootstrap.sitecustomize

        # Modify the PYTHONPATH for any subprocesses that might be spawned:
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
else:
    _log("user-installed ddtrace found, aborting", level="warning")
