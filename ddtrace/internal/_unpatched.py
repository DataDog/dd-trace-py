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

threading_Lock = _threading.Lock
threading_RLock = _threading.RLock
threading_Event = _threading.Event

# Acquire references to the socket primitives that gevent replaces when it
# monkey-patches the socket module. Capturing the attribute objects here (before
# any call to gevent.monkey.patch_all(), which is only supported after ddtrace is
# loaded) keeps ddtrace's agent I/O on real, blocking sockets regardless of
# whether the socket module is later patched. Unlike a module-level reference,
# these survive in-place patching because gevent reassigns module attributes
# rather than mutating the captured objects themselves.
import socket as _socket  # noqa

unpatched_socket = _socket.socket
unpatched_create_connection = _socket.create_connection


previous_loaded_modules = frozenset(sys.modules.keys())
from subprocess import Popen as unpatched_Popen  # noqa # nosec B404
from os import close as unpatched_close  # noqa: F401, E402

loaded_modules = frozenset(sys.modules.keys())
for module in previous_loaded_modules - loaded_modules:
    del sys.modules[module]
