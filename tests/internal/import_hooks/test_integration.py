import contextlib
import mock
import sys

import pytest

from ddtrace.internal import import_hooks


@pytest.fixture
def hooks():
    import_hooks.hooks.reset()
    try:
        yield import_hooks.hooks
    finally:
        import_hooks.hooks.reset()


@contextlib.contextmanager
def remove_module():
    was_loaded = 'urllib' in sys.modules
    try:
        # Ensure urllib is not loaded
        if was_loaded:
            del sys.modules['urllib']
        yield
    finally:
        if not was_loaded:
            del sys.modules['urllib']


def test_import_hooks(hooks):
    """
    When registering an import hook
        Gets called after the module was imported
    """
    # Ensure our module is not yet loaded
    with remove_module():
        # Register our hook (when the module is not loaded)
        module_hook = mock.Mock()
        import_hooks.register_module_hook('urllib', module_hook)

        # Import the module being hooked
        import urllib

        # Ensure we called our hook with the module
        # DEV: Slightly redundant to check twice, but good to be sure
        module_hook.assert_called_once_with(urllib)
        module_hook.assert_called_once_with(sys.modules['urllib'])
