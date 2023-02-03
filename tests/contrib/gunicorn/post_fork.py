import sys

import gevent.monkey

from ddtrace.debugging import DynamicInstrumentation
from ddtrace.internal.remoteconfig import RemoteConfig


# take some notes about the relative ordering of thread creation and
# monkeypatching
monkeypatch_happened = gevent.monkey.is_module_patched("threading")

# enabling DI here allows test cases to exercise the code paths that handle
# gevent monkeypatching of running threads
# post_fork is called before gevent.monkey.patch_all()
if sys.version_info < (3, 11):
    DynamicInstrumentation.enable()

RemoteConfig._was_enabled_after_gevent_monkeypatch = monkeypatch_happened
