# flake8: noqa
# setup logging
import logging

logging.basicConfig(level=logging.DEBUG)


# import tracer which initializes thread workers
from ddtrace import tracer


# exit process
import sys

sys.exit(0)
