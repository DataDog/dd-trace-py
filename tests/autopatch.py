""" auto patch things. """

# manual test for autopatching
import logging
import sys

# project
import ddtrace
from ddtrace.contrib.autopatch import autopatch

# allow logging
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

ddtrace.tracer.debug_logging = True

autopatch()
