import platform

import gevent.monkey

from ddtrace.debugging import DynamicInstrumentation
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.profiling.profiler import Profiler


PYTHON_VERSION = tuple(int(v) for v in platform.python_version_tuple())

# take some notes about the relative ordering of thread creation and
# monkeypatching
monkeypatch_happened = gevent.monkey.is_module_patched("threading")

# enabling DI here allows test cases to exercise the code paths that handle
# gevent monkeypatching of running threads
# post_fork is called before gevent.monkey.patch_all()
if PYTHON_VERSION[1] < 11:
    DynamicInstrumentation.enable()

RemoteConfig._was_enabled_after_gevent_monkeypatch = monkeypatch_happened
Profiler._was_enabled_after_gevent_monkeypatch = monkeypatch_happened
