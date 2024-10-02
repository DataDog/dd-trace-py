import json
import os
from pathlib import Path
import sys
from warnings import warn

import mock
import pytest

from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import origin
import tests.test_module


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _build_env():
    environ = dict(PATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR), PYTHONPATH="%s:%s" % (ROOT_PROJECT_DIR, ROOT_DIR))
    if os.environ.get("PATH"):
        environ["PATH"] = "%s:%s" % (os.environ.get("PATH"), environ["PATH"])
    if os.environ.get("PYTHONPATH"):
        environ["PYTHONPATH"] = "%s:%s" % (os.environ.get("PYTHONPATH"), environ["PYTHONPATH"])
    return environ


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
            if ModuleWatchdog.is_installed():
                warn(
                    "ModuleWatchdog still installed after test run. This might also be caused by a test that failed "
                    "while a ModuleWatchdog was installed."
                )
            else:
                ModuleWatchdog.install()


@pytest.fixture
def module_watchdog():
    ModuleWatchdog.install()

    assert ModuleWatchdog.is_installed()

    yield ModuleWatchdog

    ModuleWatchdog.uninstall()


def test_watchdog_install_uninstall():
    assert not ModuleWatchdog.is_installed()
    assert not any(isinstance(m, ModuleWatchdog) for m in sys.meta_path)

    ModuleWatchdog.install()

    assert ModuleWatchdog.is_installed()
    assert isinstance(sys.meta_path[0], ModuleWatchdog)

    ModuleWatchdog.uninstall()

    assert not ModuleWatchdog.is_installed()
    assert not any(isinstance(m, ModuleWatchdog) for m in sys.meta_path)


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


def test_after_module_imported_decorator(module_watchdog):
    hook = mock.Mock()
    module = sys.modules[__name__]
    module_watchdog.after_module_imported(module.__name__)(hook)

    hook.assert_called_once_with(module)


@pytest.mark.subprocess(env=dict(MODULE_ORIGIN=str(origin(tests.test_module))))
def test_import_origin_hook_for_module_not_yet_imported():
    import os
    from pathlib import Path
    import sys

    from mock import mock

    from ddtrace.internal.module import ModuleWatchdog

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

    from ddtrace.internal.module import ModuleWatchdog

    if "json" in sys.modules:
        del sys.modules["json"]
        gc.collect()

    name = "json"
    path = Path(os.getenv("MODULE_ORIGIN")).resolve()

    class Counter(object):
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
    assert module_watchdog._instance._hook_map[str(path)] == [hook, hook]

    module_watchdog.unregister_origin_hook(path, hook)
    assert module_watchdog._instance._hook_map[str(path)] == [hook]

    module_watchdog.unregister_origin_hook(path, hook)

    assert module_watchdog._instance._hook_map[str(path)] == []

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

    module_watchdog.unregister_module_hook(module, hook)


def test_module_watchdog_multiple_install():
    ModuleWatchdog.install()
    assert ModuleWatchdog.is_installed()
    ModuleWatchdog.install()
    assert ModuleWatchdog.is_installed()

    ModuleWatchdog.uninstall()
    assert not ModuleWatchdog.is_installed()
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
    import tests.internal.test_module  # noqa:F401

    assert {"tests", "tests.internal", "tests.internal.test_module"} <= ImportCatcher.imports, ImportCatcher.imports

    ImportCatcher.uninstall()


@pytest.mark.subprocess(
    out="post_run_module_hook OK\n",
    env=_build_env(),
    run_module=True,
)
def test_post_run_module_hook():
    # DEV: This test runs the content of the sitecustomize.py file located in
    # the same folder as this test file. The assertion logic is contained in the
    # hook that gets triggered on module load. Proof of work is given by the
    # generated output. Here we just define a module global variable to ensure
    # that the module is loaded correctly.
    post_run_module = True  # noqa:F841


def test_get_by_origin(module_watchdog):
    assert module_watchdog.get_by_origin(Path(__file__.replace(".pyc", ".py"))) is sys.modules[__name__]


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

    import tests.submod.stuff  # noqa:F401

    assert a.__modules__ >= {"tests.submod.stuff"}, a.__modules__
    assert b.__modules__ >= {"tests.submod.stuff"}, b.__modules__

    Bob.uninstall()
    Alice.uninstall()


@pytest.mark.subprocess(out="ddtrace imported\naccessing lazy module\nlazy loaded\n")
def test_module_watchdog_no_lazy_force_load():
    """Test that the module watchdog does not force-load lazy modules.

    We use the LazyLoader to load a module lazily. On actual import, the module
    emits a print statement. We check that the timing of other print statements
    around the actual import is correct to ensure that the import of ddtrace is
    not forcing the lazy module to be loaded.
    """
    import importlib.util
    import sys

    def lazy_import(name):
        spec = importlib.util.find_spec(name)
        loader = importlib.util.LazyLoader(spec.loader)
        spec.loader = loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        loader.exec_module(module)
        return module

    lazy = lazy_import("tests.internal.lazy")

    import ddtrace  # noqa:F401

    print("ddtrace imported")

    print("accessing lazy module")
    try:
        # This attribute access should cause the module to be loaded
        lazy.__spec__
    except AttributeError:
        pass


@pytest.mark.subprocess(ddtrace_run=True)
def test_module_watchdog_weakref():
    """Check that we can ignore entries in sys.modules that cannot be weakref'ed."""
    import sys

    sys.modules["bogus"] = str.__init__  # Cannot create weak ref to method_descriptor

    from ddtrace.internal.module import ModuleWatchdog

    instance = ModuleWatchdog._instance
    instance._om = None
    assert ModuleWatchdog._instance._origin_map


def test_module_watchdog_namespace_import():
    ModuleWatchdog.install()

    ns_imported = False

    def ns_hook(module):
        nonlocal ns_imported
        ns_imported = True

    ModuleWatchdog.register_module_hook("namespace_test", ns_hook)

    try:
        sys.path.insert(0, str(Path(__file__).parent))

        import namespace_test.ns_module  # noqa:F401

        assert ns_imported
    finally:
        sys.path.pop(0)
        ModuleWatchdog.uninstall()


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        PYTHONPATH=os.pathsep.join((str(Path(__file__).parent), os.environ.get("PYTHONPATH", ""))),
        PYTHONDEVMODE="1",
    ),
)
def test_module_watchdog_namespace_import_no_warnings():
    # Test that the namespace import does not emit warnings (e.g. fallback to
    # legacy import machinery).
    import namespace_test.ns_module  # noqa:F401


@pytest.mark.subprocess(ddtrace_run=True, env=dict(NSPATH=str(Path(__file__).parent)))
def test_module_watchdog_pkg_resources_support():
    # Test that we can access resource files with pkg_resources without raising
    # an exception.
    import os
    import sys

    sys.path.insert(0, os.getenv("NSPATH"))

    import pkg_resources as p

    p.resource_listdir("namespace_test.ns_module", ".")


@pytest.mark.subprocess(
    env=dict(NSPATH=str(Path(__file__).parent)),
    err=lambda _: "Can't perform this operation for unregistered loader type" not in _,
)
def test_module_watchdog_pkg_resources_support_already_imported():
    # Test that we can access resource files with pkg_resources without raising
    # an exception.
    import os
    import sys

    assert "ddtrace" not in sys.modules

    sys.path.insert(0, os.getenv("NSPATH"))

    import pkg_resources as p

    import ddtrace  # noqa

    p.resource_listdir("namespace_test.ns_module", ".")


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="importlib.resources.files is not available or broken in Python < 3.10"
)
@pytest.mark.subprocess(env=dict(NSPATH=str(Path(__file__).parent)))
def test_module_watchdog_importlib_resources_files():
    import os
    import sys

    sys.path.insert(0, os.getenv("NSPATH"))

    from importlib.readers import MultiplexedPath
    import importlib.resources as r

    assert isinstance(r.files("namespace_test"), MultiplexedPath)


@pytest.mark.subprocess
def test_module_watchdog_does_not_rewrap_get_code():
    """Ensures that self.loader.get_code() does not raise an error when the module is reloaded many times"""
    from importlib import reload

    import ddtrace  #  noqa:F401
    from tests.internal.namespace_test import ns_module

    # Check that the loader's get_code is wrapped:
    assert ns_module.__loader__.get_code._dd_get_code is True
    initial_get_code = ns_module.__loader__.get_code

    # Reload module a couple of times and check that the loader's get_code is still the same as the original
    reload(ns_module)
    reload(ns_module)
    new_get_code = ns_module.__loader__.get_code
    assert (
        new_get_code is initial_get_code
    ), f"module loader get_code (id: {id(new_get_code)}is not initial get_code (id: {id(initial_get_code)})"


@pytest.mark.subprocess
def test_module_watchdog_reloads_dont_cause_errors():
    """Ensures that self.loader.get_code() does not raise an error when the module is reloaded many times"""
    from importlib import reload
    import sys

    from tests.internal.namespace_test import ns_module

    # Since this test is running in a subprocess, the odds that the recursionlimit gets modified are low, so we set it
    # to a reasonably low number, but still loop higher to make sure we don't hit the limit.
    sys.setrecursionlimit(1000)
    for _ in range(sys.getrecursionlimit() * 2):
        reload(ns_module)


@pytest.mark.subprocess(ddtrace_run=True)
def test_module_import_side_effect():
    # Test that we can import a module that raises an exception during specific
    # attribute lookups.
    import tests.internal.side_effect_module  # noqa:F401


def test_deprecated_modules_in_ddtrace_contrib():
    # Test that all files in the ddtrace/contrib directory except a few exceptions (ex: ddtrace/contrib/redis_utils.py)
    # have the deprecation template below.
    deprecation_template = """from ddtrace.contrib.internal.{} import *  # noqa: F403
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    deprecate(
        ("%s.%s is deprecated" % (__name__, name)),
        category=DDTraceDeprecationWarning,
    )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)
"""

    contrib_dir = Path(ROOT_PROJECT_DIR) / "ddtrace" / "contrib"

    missing_deprecations = set()
    for directory, _, file_names in os.walk(contrib_dir):
        if directory.startswith(str(contrib_dir / "internal")):
            # Files in ddtrace/contrib/internal/... are not part of the public API, they should not be deprecated
            continue
        # Open files in ddtrace/contrib/ and check if the content matches the template
        for file_name in file_names:
            # Skip internal and __init__ modules, as they are not supposed to have the deprecation template
            if file_name.endswith(".py") and not (file_name.startswith("_") or file_name == "__init__.py"):
                # Get the relative path of the file from ddtrace/contrib to the deprecated file (ex: pymongo/patch)
                relative_path = Path(directory).relative_to(contrib_dir) / file_name[:-3]  # Remove the .py extension
                # Convert the relative path to python module format (ex: [pymongo, patch] -> pymongo.patch)
                sub_modules = ".".join(relative_path.parts)
                with open(os.path.join(directory, file_name), "r") as f:
                    content = f.read()
                    if deprecation_template.format(sub_modules) != content:
                        missing_deprecations.add(f"ddtrace.contrib.{sub_modules}")

    assert missing_deprecations == set(
        [
            # Note: The following ddtrace.contrib modules are expected to be part of the public API
            # TODO: Revist whether integration utils should be part of the public API
            "ddtrace.contrib.redis_utils",
            "ddtrace.contrib.trace_utils",
            "ddtrace.contrib.trace_utils_async",
            "ddtrace.contrib.trace_utils_redis",
            # TODO: The following contrib modules are part of the public API (unlike most integrations).
            # We should consider privatizing the internals of these integrations.
            "ddtrace.contrib.unittest.patch",
            "ddtrace.contrib.unittest.constants",
            "ddtrace.contrib.pytest.constants",
            "ddtrace.contrib.pytest.newhooks",
            "ddtrace.contrib.pytest.plugin",
            "ddtrace.contrib.pytest_benchmark.constants",
            "ddtrace.contrib.pytest_benchmark.plugin",
            "ddtrace.contrib.pytest_bdd.constants",
            "ddtrace.contrib.pytest_bdd.plugin",
        ]
    )
