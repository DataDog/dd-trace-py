import importlib
import json
from pathlib import Path
import sys
from unittest import mock

import pytest

from ddtrace.debugging._import import DebuggerModuleWatchdog
from ddtrace.internal.module import origin
import tests.test_module


@pytest.fixture
def module_watchdog():
    DebuggerModuleWatchdog.install()
    try:
        yield DebuggerModuleWatchdog
    finally:
        DebuggerModuleWatchdog.uninstall()


def test_import_origin_hook_for_imported_module(module_watchdog):
    hook = mock.Mock()
    module = sys.modules[__name__]
    module_watchdog.register_origin_hook(origin(module), hook)

    hook.assert_called_once_with(module)


def test_origin_hook_after_name_only_import(tmp_path, monkeypatch):
    from ddtrace.internal.module import ModuleWatchdog

    module_watchdog = DebuggerModuleWatchdog
    name = "watchdog_late_origin"
    path = tmp_path / (name + ".py")
    path.write_text("value = 42\n")
    alias = tmp_path / "alias.py"
    alias.symlink_to(path)
    monkeypatch.syspath_prepend(str(tmp_path))
    name_hook = mock.Mock()
    origin_hook = mock.Mock()
    ModuleWatchdog.register_module_hook(name, name_hook)

    try:
        module = importlib.import_module(name)
        module_watchdog.register_origin_hook(alias, origin_hook)
        origin_hook.assert_called_once_with(module)
        assert module_watchdog.get_by_origin(path) is module

        importlib.reload(module)
        assert origin_hook.call_count == 2
        assert name_hook.call_count == 2

        del sys.modules[name]
        replacement = importlib.import_module(name)
        assert replacement is not module
        assert module_watchdog.get_by_origin(alias) is replacement
        assert origin_hook.call_count == 3
        assert name_hook.call_count == 3
    finally:
        ModuleWatchdog.unregister_module_hook(name, name_hook)
        DebuggerModuleWatchdog.uninstall()
        sys.modules.pop(name, None)


@pytest.mark.subprocess(env=dict(MODULE_ORIGIN=str(origin(tests.test_module))))
def test_import_origin_hook_for_module_not_yet_imported():
    import os
    from pathlib import Path
    import sys
    from unittest import mock

    from ddtrace.debugging._import import DebuggerModuleWatchdog as ModuleWatchdog

    name = "tests.test_module"
    path = Path(os.getenv("MODULE_ORIGIN"))
    hook = mock.Mock()

    ModuleWatchdog.register_origin_hook(path, hook)

    hook.assert_not_called()
    assert str(path) in ModuleWatchdog._instance._hook_map
    assert name not in sys.modules

    # Check that we are not triggering hooks on the wrong module
    import tests.internal  # noqa:F401

    hook.assert_not_called()

    # We import multiple times to check that the hook is called once only
    __import__(name)
    __import__(name)

    assert name in sys.modules

    hook.assert_called_once_with(sys.modules[name])

    ModuleWatchdog.uninstall()


@pytest.mark.subprocess(env=dict(MODULE_ORIGIN=str(origin(json))))
def test_module_deleted():
    import gc
    import os
    from pathlib import Path
    import sys

    from ddtrace.debugging._import import DebuggerModuleWatchdog as ModuleWatchdog

    if "json" in sys.modules:
        del sys.modules["json"]
        gc.collect()

    name = "json"
    path = Path(os.getenv("MODULE_ORIGIN")).resolve()

    class Counter:
        count = 0

        def __call__(self, _):
            self.count += 1

    hook = Counter()

    ModuleWatchdog.register_origin_hook(path, hook)
    ModuleWatchdog.register_module_hook(name, hook)

    __import__(name)

    assert hook.count == 2, hook.count

    assert str(path) in ModuleWatchdog._instance._origin_map

    del sys.modules[name]
    gc.collect()
    assert name not in sys.modules

    assert str(path) not in ModuleWatchdog._instance._origin_map

    # We are not deleting the registered hooks, so if we re-import the module
    # new hook calls are triggered
    __import__(name)

    assert hook.count == 4

    ModuleWatchdog.uninstall()


def test_module_unregister_origin_hook(module_watchdog):
    hook = mock.Mock()
    path = origin(sys.modules[__name__])

    module_watchdog.register_origin_hook(path, hook)
    assert module_watchdog._instance._hook_map[str(path)] == [hook]

    module_watchdog.register_origin_hook(path, hook)
    assert module_watchdog._instance._hook_map[str(path)] == [hook]
    assert hook.call_count == 2

    module_watchdog.unregister_origin_hook(path, hook)

    assert str(path) not in module_watchdog._instance._hook_map

    module_watchdog.unregister_origin_hook(path, hook)


def test_get_by_origin(module_watchdog):
    assert module_watchdog.get_by_origin(Path(__file__.replace(".pyc", ".py"))) is sys.modules[__name__]


@pytest.mark.subprocess(ddtrace_run=True)
def test_module_watchdog_weakref():
    """Check that we can ignore entries in sys.modules that cannot be weakref'ed."""
    import sys

    sys.modules["bogus"] = str.__init__  # Cannot create weak ref to method_descriptor

    from ddtrace.debugging._import import DebuggerModuleWatchdog as ModuleWatchdog

    ModuleWatchdog.install()
    instance = ModuleWatchdog._instance
    instance._om = None
    assert ModuleWatchdog._instance._origin_map
    ModuleWatchdog.uninstall()
