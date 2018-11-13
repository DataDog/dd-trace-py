import os
import sys

sys.path.append('.')
from ddtrace.commands import ddtrace_run

os.environ['PYTHONPATH'] = "{}:{}".format(os.getenv('PYTHONPATH'), os.path.abspath('.'))
ddtrace_run.main()
