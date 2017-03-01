#!/usr/bin/env python
from __future__ import print_function

from distutils import spawn
import os
import sys
import logging

debug = os.environ.get("DATADOG_TRACE_DEBUG")
if debug and debug.lower() == "true":
    logging.basicConfig(level=logging.DEBUG)

log = logging.getLogger(__name__)

USAGE = """
Usage: [ENV_VARS] ddtrace-run <my_program>

Available environment variables:

    DATADOG_ENV : override an application's environment (no default)
    DATADOG_TRACE_ENABLED=true|false : override the value of tracer.enabled (default: true)
    DATADOG_TRACE_DEBUG=true|false : override the value of tracer.debug_logging (default: false)
    DATADOG_SERVICE_NAME : override the service name to be used for this program (no default)
                           This value is passed through when setting up middleware for web framework integrations.
                           (e.g. pylons, flask, django)
                           For tracing without a web integration, prefer setting the service name in code.
"""

def _ddtrace_root():
    from ddtrace import __file__
    return os.path.dirname(__file__)


def _add_bootstrap_to_pythonpath(bootstrap_dir):
    """
    Add our bootstrap directory to the head of $PYTHONPATH to ensure
    it is loaded before program code
    """
    python_path = os.environ.get('PYTHONPATH', '')

    if python_path:
        new_path = "%s%s%s" % (bootstrap_dir, os.path.pathsep,
                os.environ['PYTHONPATH'])
        os.environ['PYTHONPATH'] = new_path
    else:
        os.environ['PYTHONPATH'] = bootstrap_dir


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        return

    log.debug("sys.argv: %s", sys.argv)

    root_dir = _ddtrace_root()
    log.debug("ddtrace root: %s", root_dir)

    bootstrap_dir = os.path.join(root_dir, 'bootstrap')
    log.debug("ddtrace bootstrap: %s", bootstrap_dir)

    _add_bootstrap_to_pythonpath(bootstrap_dir)
    log.debug("PYTHONPATH: %s", os.environ['PYTHONPATH'])
    log.debug("sys.path: %s", sys.path)

    executable = sys.argv[1]

    # Find the executable path
    executable = spawn.find_executable(executable)
    log.debug("program executable: %s", executable)

    if 'DATADOG_SERVICE_NAME' not in os.environ:
        # infer service name from program command-line
        service_name = os.path.basename(executable)
        os.environ['DATADOG_SERVICE_NAME'] = service_name

    os.execl(executable, executable, *sys.argv[2:])
