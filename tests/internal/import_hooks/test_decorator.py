import mock
import pytest

from ddtrace.internal import import_hooks
from ddtrace.internal.import_hooks.decorator import register_module_hook


@pytest.fixture
def hooks():
    try:
        yield import_hooks.hooks
    finally:
        import_hooks.hooks.reset()


def test_register_module_hook_func(hooks):
    """
    When registering a module hook function
        Passing the hook as a parameter
            We properly register the hook
    """
    # Create and register a module hook
    module_hook = mock.Mock()
    hook_res = register_module_hook('module', module_hook)

    # Ensure the returned value is our original function
    assert hook_res == module_hook

    # Ensure the hook was registered
    module = object()
    hooks.call('module', module=module)
    module_hook.assert_called_once_with(module)


def test_register_module_hook_decorator(hooks):
    """
    When registering a module hook function
        Using a decorator to register the hook
            We properly register the hook
    """
    # Create and register a module hook
    module_hook = mock.Mock()

    # DEV: This is the same as:
    #   @register_module_hook('module')
    #   def module_hook(module):
    #       pass
    hook_res = register_module_hook('module')(module_hook)

    # Ensure the returned value is our original function
    assert hook_res == module_hook

    # Ensure the hook was registered
    module = object()
    hooks.call('module', module=module)
    module_hook.assert_called_once_with(module)
