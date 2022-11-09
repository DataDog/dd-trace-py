from os.path import dirname
import sys

import mock
import pytest

from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import origin
import tests.test_module


@pytest.fixture(autouse=True, scope="module")
def ensure_no_module_watchdog():
    # DEV: The library might use the ModuleWatchdog and install it at a very
    # early stage. This fixture ensures that the watchdog is not installed
    # before the tests start.
    was_installed = ModuleWatchdog.is_installed()
    if was_installed:
        ModuleWatchdog.uninstall()

    try:
        yield
    finally:
        if was_installed:
            ModuleWatchdog.install()


@pytest.fixture
def module_watchdog():
    ModuleWatchdog.install()

    assert ModuleWatchdog.is_installed()

    yield ModuleWatchdog

    ModuleWatchdog.uninstall()


def test_watchdog_install_uninstall():
    assert not isinstance(sys.modules, ModuleWatchdog)
    ModuleWatchdog.install()
    assert isinstance(sys.modules, ModuleWatchdog)
    ModuleWatchdog.uninstall()
    assert not isinstance(sys.modules, ModuleWatchdog)


def test_import_origin_hook_for_imported_module(module_watchdog):
    hook = mock.Mock()
    module = sys.modules[__name__]
    module_watchdog.register_origin_hook(origin(module), hook)

    hook.assert_called_once_with(module)


def test_import_module_hook_for_imported_module(module_watchdog):
    hook = mock.Mock()
    module = sys.modules[__name__]
    module_watchdog.register_module_hook(module.__name__, hook)

    hook.assert_called_once_with(module)


def test_register_hook_without_install():
    with pytest.raises(RuntimeError):
        ModuleWatchdog.register_origin_hook(__file__, mock.Mock())

    with pytest.raises(RuntimeError):
        ModuleWatchdog.register_module_hook(__name__, mock.Mock())


@pytest.mark.subprocess(env=dict(MODULE_ORIGIN=origin(tests.test_module)))
def test_import_origin_hook_for_module_not_yet_imported():
    import os
    import sys

    from mock import mock

    from ddtrace.internal.module import ModuleWatchdog

    name = "tests.test_module"
    path = os.getenv("MODULE_ORIGIN")
    hook = mock.Mock()

    ModuleWatchdog.register_origin_hook(path, hook)

    hook.assert_not_called()
    assert path in ModuleWatchdog._instance._hook_map
    assert name not in sys.modules

    # Check that we are not triggering hooks on the wrong module
    import tests.internal  # noqa

    hook.assert_not_called()

    # We import multiple times to check that the hook is called once only
    __import__(name)
    __import__(name)

    assert name in sys.modules

    hook.assert_called_once_with(sys.modules[name])

    ModuleWatchdog.uninstall()


@pytest.mark.subprocess
def test_import_module_hook_for_module_not_yet_imported():
    import sys

    from mock import mock

    from ddtrace.internal.module import ModuleWatchdog

    name = "tests.test_module"
    hook = mock.Mock()

    ModuleWatchdog.register_module_hook(name, hook)

    hook.assert_not_called()
    assert name not in sys.modules

    # Check that we are not triggering hooks on the wrong module
    import tests.internal  # noqa

    hook.assert_not_called()

    # We import multiple times to check that the hook is called once only
    __import__(name)
    __import__(name)

    assert name in sys.modules

    hook.assert_called_once_with(sys.modules[name])

    ModuleWatchdog.uninstall()


@pytest.mark.subprocess(env=dict(MODULE_ORIGIN=origin(tests.test_module)))
def test_module_deleted():
    import os
    import sys

    from mock import mock

    from ddtrace.internal.module import ModuleWatchdog

    name = "tests.test_module"
    path = os.getenv("MODULE_ORIGIN")
    hook = mock.Mock()

    ModuleWatchdog.register_origin_hook(path, hook)
    ModuleWatchdog.register_module_hook(name, hook)

    __import__(name)

    calls = [mock.call(sys.modules[name])] * 2
    hook.assert_has_calls(calls)

    assert path in ModuleWatchdog._instance._origin_map

    del sys.modules[name]

    assert path not in ModuleWatchdog._instance._origin_map

    # We are not deleting the registered hooks, so if we re-import the module
    # new hook calls are triggered
    __import__(name)

    calls.extend([mock.call(sys.modules[name])] * 2)
    hook.assert_has_calls(calls)

    ModuleWatchdog.uninstall()


def test_module_unregister_origin_hook(module_watchdog):

    hook = mock.Mock()
    path = origin(sys.modules[__name__])

    module_watchdog.register_origin_hook(path, hook)
    assert module_watchdog._instance._hook_map[path] == [hook]

    module_watchdog.register_origin_hook(path, hook)
    assert module_watchdog._instance._hook_map[path] == [hook, hook]

    module_watchdog.unregister_origin_hook(path, hook)
    assert module_watchdog._instance._hook_map[path] == [hook]

    module_watchdog.unregister_origin_hook(path, hook)

    assert module_watchdog._instance._hook_map[path] == []

    with pytest.raises(ValueError):
        module_watchdog.unregister_origin_hook(path, hook)


def test_module_unregister_module_hook(module_watchdog):

    hook = mock.Mock()
    module = __name__
    module_watchdog.register_module_hook(module, hook)
    assert module_watchdog._instance._hook_map[module] == [hook]

    module_watchdog.register_module_hook(module, hook)
    assert module_watchdog._instance._hook_map[module] == [hook, hook]

    module_watchdog.unregister_module_hook(module, hook)
    assert module_watchdog._instance._hook_map[module] == [hook]

    module_watchdog.unregister_module_hook(module, hook)
    assert module_watchdog._instance._hook_map[module] == []

    with pytest.raises(ValueError):
        module_watchdog.unregister_module_hook(module, hook)


def test_module_watchdog_multiple_install():
    ModuleWatchdog.install()
    with pytest.raises(RuntimeError):
        ModuleWatchdog.install()

    assert ModuleWatchdog.is_installed()

    ModuleWatchdog.uninstall()
    with pytest.raises(RuntimeError):
        ModuleWatchdog.uninstall()

    assert not ModuleWatchdog.is_installed()


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


@pytest.mark.subprocess(
    out="post_run_module_hook OK\n",
    env=dict(PYTHONPATH=dirname(__file__)),
    run_module=True,
)
def test_post_run_module_hook():
    # DEV: This test runs the content of the sitecustomize.py file located in
    # the same folder as this test file. The assertion logic is contained in the
    # hook that gets triggered on module load. Proof of work is given by the
    # generated output. Here we just define a module global variable to ensure
    # that the module is loaded correctly.
    post_run_module = True  # noqa


def test_get_by_origin(module_watchdog):
    assert module_watchdog.get_by_origin(__file__.replace(".pyc", ".py")) is sys.modules[__name__]


@pytest.mark.subprocess
def test_module_watchdog_propagation():
    # Test that the module watchdog propagates the module hooks to each
    # installed subclass.
    from ddtrace.internal.module import ModuleWatchdog

    class BaseCollector(ModuleWatchdog):
        def __init__(self):
            self.__modules__ = set()
            super(BaseCollector, self).__init__()

        def after_import(self, module):
            # We save the module name as proof that the after_import method
            # was called on the subclass instance.
            self.__modules__.add(module.__name__)
            return super(BaseCollector, self).after_import(module)

    class Alice(BaseCollector):
        pass

    class Bob(BaseCollector):
        pass

    Alice.install()
    Bob.install()

    a = Alice._instance
    b = Bob._instance

    import tests.submod.stuff  # noqa

    assert a.__modules__ >= {"tests.submod.stuff"}, a.__modules__
    assert b.__modules__ >= {"tests.submod.stuff"}, b.__modules__

    Bob.uninstall()
    Alice.uninstall()


def test_module_watchdog_dict_shallow_copy():
    # Save original reference to sys.modules
    original_sys_modules = sys.modules

    ModuleWatchdog.install()

    # Ensure that we have replaced sys.modules
    assert original_sys_modules is not sys.modules

    # Make a shallow copy of both using the dict constructor
    original_modules = set(dict(original_sys_modules).keys())
    new_modules = set(dict(sys.modules).keys())

    # Ensure that they match
    assert original_modules == new_modules

    ModuleWatchdog.uninstall()
