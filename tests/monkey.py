""" auto patch things. """

# manual test for monkey patching
import logging
import sys

# project
import ddtrace

# allow logging
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)

ddtrace.tracer.debug_logging = True

# Patch nothing
ddtrace.patch()

# Patch all except Redis
ddtrace.patch_all(redis=False)

# Patch Redis
ddtrace.patch(redis=True)
