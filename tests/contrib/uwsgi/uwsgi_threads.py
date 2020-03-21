# flake8: noqa
# setup logging
import logging
import sys

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)


# import the uwsgi module test its function from within the test app
# and ensure it can produce debug logs
from ddtrace.utils import uwsgi
uwsgi.check_threads_enabled()

# import tracer which initializes thread workers
from ddtrace import tracer

# exit process
sys.exit(0)
