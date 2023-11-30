from pathlib import Path
import sys

from output import log


# Make the tests/debugging folder available as a module. We don't want to
# go as further back as tests/ because we are likely to override a folder with
# a similar name from the target project. That's because we install the
# frameworks in edit mode and run the test suite from their root folder, which
# is likely to contain a tests/ folder. Hence, if we insert our our tests/
# folder we risk breaking the framework's tests; whereas if we append it, we
# risk breaking our exploration tests.

sys.path.append(str(Path(__file__).parents[2].resolve()))


import _coverage  # noqa:E402,F401
import _profiler  # noqa:E402,F401


log("Enabling debugging exploration testing")
