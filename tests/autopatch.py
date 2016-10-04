
# manual test for autopatching
import logging
logging.basicConfig(level=logging.DEBUG)

from ddtrace.contrib.autopatch import autopatch

autopatch()
