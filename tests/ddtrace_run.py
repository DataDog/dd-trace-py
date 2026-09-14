import sys


# DEV: We must append to sys path before importing ddtrace_run
sys.path.append(".")
from ddtrace.commands import ddtrace_run  # noqa:E402


ddtrace_run.main()
