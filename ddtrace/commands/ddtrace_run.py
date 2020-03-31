#!/usr/bin/env python
from distutils import spawn
import os
import sys
import logging

debug = os.environ.get('DATADOG_TRACE_DEBUG')
if debug and debug.lower() == 'true':
    logging.basicConfig(level=logging.DEBUG)

# Do not use `ddtrace.internal.logger.get_logger` here
# DEV: It isn't really necessary to use `DDLogger` here so we want to
#        defer importing `ddtrace` until we actually need it.
#      As well, no actual rate limiting would apply here since we only
#        have a few logged lines
log = logging.getLogger(__name__)

USAGE = """
Execute the given Python program after configuring it to emit Datadog traces.
Append command line arguments to your program as usual.

Usage: [ENV_VARS] ddtrace-run <my_program>

Available environment variables:

    DATADOG_TRACE_ENABLED=true|false : override the value of tracer.enabled (default: true)
    DD_ENV : set an application's environment (no default)
    DD_SERVICE: set the service name to be used for this application (default only for select frameworks)
    DD_VERSION: set an application's version to be added to traces and logs (no default)
    DATADOG_TRACE_DEBUG=true|false : enabled debug logging (default: false)
    DATADOG_PATCH_MODULES=module:patch,module:patch... e.g. boto:true,redis:false : override the modules patched for this execution of the program (default: none)
    DD_TRACE_AGENT_URL=http://localhost:8126: override the address of the trace agent host that the default tracer will attempt to submit to  (default: http://localhost:8126)
    DATADOG_PRIORITY_SAMPLING=true|false: enables Priority Sampling. (default: false)
    DD_LOGS_INJECTION=true|false: enable injecting trace information into log records to correlate. (default: false)
"""  # noqa: E501


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
        new_path = '%s%s%s' % (bootstrap_dir, os.path.pathsep, os.environ['PYTHONPATH'])
        os.environ['PYTHONPATH'] = new_path
    else:
        os.environ['PYTHONPATH'] = bootstrap_dir


def main():
    if len(sys.argv) < 2 or sys.argv[1] == '-h':
        print(USAGE)
        return

    log.debug('sys.argv: %s', sys.argv)

    root_dir = _ddtrace_root()
    log.debug('ddtrace root: %s', root_dir)

    bootstrap_dir = os.path.join(root_dir, 'bootstrap')
    log.debug('ddtrace bootstrap: %s', bootstrap_dir)

    _add_bootstrap_to_pythonpath(bootstrap_dir)
    log.debug('PYTHONPATH: %s', os.environ['PYTHONPATH'])
    log.debug('sys.path: %s', sys.path)

    executable = sys.argv[1]

    # Find the executable path
    executable = spawn.find_executable(executable)
    log.debug('program executable: %s', executable)

    os.execl(executable, executable, *sys.argv[2:])
