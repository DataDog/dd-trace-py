# Acquire a reference to the open function from the builtins module. This is
# necessary to ensure that the open function can be used unpatched when required.
from builtins import open as unpatched_open  # noqa
from json import loads as unpatched_json_loads  # noqa

# Acquire a reference to the threading module. Some parts of the library (e.g.
# the profiler) might be enabled programmatically and therefore might end up
# getting a reference to the tracee's threading module. By storing a reference
# to the threading module used by ddtrace here, we make it easy for those parts
# to get a reference to the right threading module.
import threading as _threading  # noqa
import gc as _gc  # noqa

import sys

previous_loaded_modules = frozenset(sys.modules.keys())
from subprocess import Popen as unpatched_Popen  # noqa # nosec B404

loaded_modules = frozenset(sys.modules.keys())
for module in previous_loaded_modules - loaded_modules:
    del sys.modules[module]
