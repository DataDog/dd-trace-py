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

# perhaps go with the basic sample app example and then use subprocess to send traces with curl


# exit process
import sys

sys.exit(0)
