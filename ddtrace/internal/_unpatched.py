# Acquire a reference to the open function from the builtins module. This is
# necessary to ensure that the open function can be used unpatched when required.
from builtins import open as unpatched_open  # noqa
from json import loads as unpatched_json_loads  # noqa

# Acquire references to threading and gc, then remove them from sys.modules.
# Some parts of the library (e.g. the profiler) might be enabled programmatically
# and patch the threading and gc modules. We store references to the original
# modules here to allow ddtrace internal modules to access the right versions of
# the modules.

import gc  # noqa
import sys
import threading  # noqa

_threading = sys.modules.pop("threading", None)
_gc = sys.modules.pop("gc", None)

previous_loaded_modules = frozenset(sys.modules.keys())
from subprocess import Popen as unpatched_Popen  # noqa # nosec B404
from os import close as unpatched_close  # noqa: F401, E402

loaded_modules = frozenset(sys.modules.keys())
for module in previous_loaded_modules - loaded_modules:
    del sys.modules[module]
