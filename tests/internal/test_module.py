from contextlib import contextmanager
from os.path import dirname
import sys

import mock
import pytest

from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import origin


@pytest.fixture
def module_watchdog():
    ModuleWatchdog.install()

    yield sys.modules

    ModuleWatchdog.uninstall()


@contextmanager
def unloaded_modules(modules):
    module_loaded = [module in sys.modules for module in modules]
    for module in modules:
        try:
            del sys.modules[module]
        except KeyError:
            pass
        assert module not in sys.modules

    yield

    for module, was_loaded in zip(modules, module_loaded):
        if was_loaded:
            __import__(module)


@contextmanager
def no_pytest_meta_path_finder():
    # ModuleWatchdog puts its MetaPathFinder in front of pytest one.
    pytest_meta_path = sys.meta_path.pop(1)
    yield
    sys.meta_path.insert(1, pytest_meta_path)


def test_watchdog_install_uninstall():
    assert not isinstance(sys.modules, ModuleWatchdog)
    ModuleWatchdog.install()
    assert isinstance(sys.modules, ModuleWatchdog)
    ModuleWatchdog.uninstall()
    assert not isinstance(sys.modules, ModuleWatchdog)


def test_import_origin_hook_for_imported_module(module_watchdog):
    hook = mock.Mock()
    module = sys.modules[__name__]
    module_watchdog.register_origin_hook(origin(module), hook, 10)

    hook.assert_called_once_with(module, 10)


def test_import_module_hook_for_imported_module(module_watchdog):
    hook = mock.Mock()
    module = sys.modules[__name__]
    module_watchdog.register_module_hook(module.__name__, hook, 10)

    hook.assert_called_once_with(module, 10)


def test_import_origin_hook_for_module_not_yet_imported(module_watchdog):
    name = "tests.test_module"
    __import__(name)
    path = origin(sys.modules[name])
    with unloaded_modules([name]):
        hook = mock.Mock()
        module_watchdog.register_origin_hook(path, hook, 42)

        hook.assert_not_called()
        assert path in module_watchdog._hook_map

        assert name not in sys.modules
        with no_pytest_meta_path_finder():
            __import__(name)
            __import__(name)
        assert name in sys.modules

        hook.assert_called_once_with(sys.modules[name], 42)


def test_import_module_hook_for_module_not_yet_imported(module_watchdog):
    name = "tests.test_module"
    __import__(name)
    with unloaded_modules([name]):
        hook = mock.Mock()
        module_watchdog.register_module_hook(name, hook, 42)

        hook.assert_not_called()
        assert name in module_watchdog._hook_map

        assert name not in sys.modules
        with no_pytest_meta_path_finder():
            __import__(name)
            __import__(name)
        assert name in sys.modules

        hook.assert_called_once_with(sys.modules[name], 42)


def test_module_deleted(module_watchdog):
    name = "tests.test_module"
    with unloaded_modules([name]), no_pytest_meta_path_finder():
        __import__(name)
        path = origin(sys.modules[name])

        hook = mock.Mock()
        module_watchdog.register_origin_hook(path, hook, 42)
        module_watchdog.register_module_hook(name, hook, 42)

        del sys.modules[name]

        assert path not in module_watchdog._origin_map


def test_module_unregister_origin_hook(module_watchdog):

    hook = mock.Mock()
    path = origin(sys.modules[__name__])
    module_watchdog.register_origin_hook(path, hook, 42)
    module_watchdog.register_origin_hook(path, hook, 43)
    module_watchdog.register_origin_hook(path, hook, 44)

    module_watchdog.unregister_origin_hook(path, 43)

    with pytest.raises(ValueError):
        module_watchdog.unregister_origin_hook(path, 45)

    assert module_watchdog._hook_map[path] == [(hook, 42), (hook, 44)]


def test_module_unregister_module_hook(module_watchdog):

    hook = mock.Mock()
    module = __name__
    module_watchdog.register_module_hook(module, hook, 42)
    module_watchdog.register_module_hook(module, hook, 43)
    module_watchdog.register_module_hook(module, hook, 44)

    module_watchdog.unregister_module_hook(module, 43)

    with pytest.raises(ValueError):
        module_watchdog.unregister_module_hook(module, 45)

    assert module_watchdog._hook_map[module] == [(hook, 42), (hook, 44)]


def test_module_watchdog_multiple_install():
    n = 3
    for _ in range(n):
        ModuleWatchdog.install()

    i = 0
    while ModuleWatchdog.is_installed() and i < n:
        ModuleWatchdog.uninstall()
        i += 1

    assert not ModuleWatchdog.is_installed()
    assert i == n


def test_module_watchdog_subclasses():
    class MyWatchdog(ModuleWatchdog):
        pass

    ModuleWatchdog.install()
    MyWatchdog.install()

    ModuleWatchdog.uninstall()
    assert not ModuleWatchdog.is_installed()

    MyWatchdog.uninstall()
    assert not MyWatchdog.is_installed()

    assert not isinstance(sys.modules, ModuleWatchdog)


def test_get_by_origin(module_watchdog):
    assert module_watchdog.get_by_origin(__file__) is sys.modules[__name__]


@pytest.mark.subprocess
def test_module_import_hierarchy():
    from ddtrace.internal.module import ModuleWatchdog

    class ImportCatcher(ModuleWatchdog):
        imports = set()

        def after_import(self, module):
            self.imports.add(module.__name__)
            return super(ImportCatcher, self).after_import(module)

    ImportCatcher.install()

    # Import a nested module to check that we are catching the import of the
    # parents as well
    import tests.internal.test_module  # noqa

    assert {"tests", "tests.internal", "tests.internal.test_module"} <= ImportCatcher.imports, ImportCatcher.imports

    ImportCatcher.uninstall()


@pytest.mark.subprocess(env={"PYTHONPATH": dirname(__file__)}, run_module=True)
def test_post_run_module_hook():
    post_run_module = True  # noqa
