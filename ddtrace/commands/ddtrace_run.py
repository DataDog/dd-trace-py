#!/usr/bin/env python
import argparse
from distutils import spawn
import logging
import os
import sys

import ddtrace
from ddtrace.compat import PY2
from ddtrace.utils.formats import asbool
from ddtrace.utils.formats import get_env


if PY2:
    # Python 2 does not have PermissionError but Python 3 does.
    class PermissionError(OSError):  # noqa
        pass


# Do not use `ddtrace.internal.logger.get_logger` here
# DEV: It isn't really necessary to use `DDLogger` here so we want to
#        defer importing `ddtrace` until we actually need it.
#      As well, no actual rate limiting would apply here since we only
#        have a few logged lines
log = logging.getLogger(__name__)

USAGE = """
Execute the given Python command after configuring it to emit Datadog traces
and profiles.


Examples
ddtrace-run python app.py
ddtrace-run gunicorn myproject.wsgi
"""


def _add_bootstrap_to_pythonpath(bootstrap_dir):
    """
    Add our bootstrap directory to the head of $PYTHONPATH to ensure
    it is loaded before program code
    """
    python_path = os.environ.get("PYTHONPATH", "")

    if python_path:
        new_path = "%s%s%s" % (bootstrap_dir, os.path.pathsep, os.environ["PYTHONPATH"])
        os.environ["PYTHONPATH"] = new_path
    else:
        os.environ["PYTHONPATH"] = bootstrap_dir


def main():
    parser = argparse.ArgumentParser(
        description=USAGE,
        prog="ddtrace-run",
        usage="ddtrace-run <your usual python command>",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, type=str, help="Command string to execute.")
    parser.add_argument("-d", "--debug", help="enable debug mode (disabled by default)", action="store_true")
    parser.add_argument("-i", "--info", help="print library info useful for debugging", action="store_true")
    parser.add_argument("-p", "--profiling", help="enable profiling (disabled by default)", action="store_true")
    parser.add_argument("-v", "--version", action="version", version="%(prog)s " + ddtrace.__version__)
    args = parser.parse_args()

    if args.profiling:
        os.environ["DD_PROFILING_ENABLED"] = "true"

    debug_mode = args.debug or asbool(get_env("trace", "debug", default=False))

    if debug_mode:
        logging.basicConfig(level=logging.DEBUG)
        os.environ["DD_TRACE_DEBUG"] = "true"

    if args.info:
        # Inline imports for performance.
        import pprint

        from ddtrace.internal.debug import collect

        pprint.pprint(collect(ddtrace.tracer))
        sys.exit(0)

    root_dir = os.path.dirname(ddtrace.__file__)
    log.debug("ddtrace root: %s", root_dir)

    bootstrap_dir = os.path.join(root_dir, "bootstrap")
    log.debug("ddtrace bootstrap: %s", bootstrap_dir)

    _add_bootstrap_to_pythonpath(bootstrap_dir)
    log.debug("PYTHONPATH: %s", os.environ["PYTHONPATH"])
    log.debug("sys.path: %s", sys.path)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Find the executable path
    executable = spawn.find_executable(args.command[0])
    if not executable:
        print("ddtrace-run: failed to find executable '%s'.\n" % args.command[0])
        parser.print_usage()
        sys.exit(1)

    log.debug("program executable: %s", executable)

    if os.path.basename(executable) == "uwsgi":
        print(
            (
                "ddtrace-run has known compatibility issues with uWSGI where the "
                "tracer is not started properly in uWSGI workers which can cause "
                "broken behavior. It is recommended you remove ddtrace-run and "
                "update your uWSGI configuration following "
                "https://ddtrace.readthedocs.io/en/stable/advanced_usage.html#uwsgi."
            )
        )

    try:
        # Raises OSError for permissions errors in Python 2
        #        PermissionError for Python 3
        os.execl(executable, executable, *args.command[1:])
    except (OSError, PermissionError):
        print("ddtrace-run: executable '%s' does not have executable permissions.\n" % executable)
        parser.print_usage()
        sys.exit(1)

    sys.exit(0)
