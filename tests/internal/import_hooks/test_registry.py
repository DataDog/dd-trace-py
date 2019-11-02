import mock
import sys

import pytest

from ddtrace.internal.import_hooks import ModuleHookRegistry
from ddtrace.internal.import_hooks import hooks as global_hooks


@pytest.fixture
def hooks():
    return ModuleHookRegistry()


@pytest.fixture
def module_hook():
    return mock.Mock()


def test_global_hooks():
    """
    When importing the global default hook registry
        It is an instance of ModuleHookRegistry
        Is has no hooks registered
    """
    # Is an instance of expected class
    assert isinstance(global_hooks, ModuleHookRegistry)

    # No hooks are registered by default
    assert len(global_hooks.hooks) == 0


def test_registry_init(hooks):
    """
    When initializing a new registry hook
        No hooks are added by default
    """
    assert len(hooks.hooks) == 0


def test_registry_register(hooks, module_hook):
    """
    When registering a module hook
        When the module is not already loaded
            We register the hook for the module name
            The module hook is not called
    """
    module_name = 'test.module.name'
    hooks.register(module_name, module_hook)

    # The hook was registered
    assert len(hooks.hooks) == 1
    assert module_name in hooks.hooks
    assert len(hooks.hooks[module_name]) == 1
    assert hooks.hooks[module_name] == set([module_hook])

    # The hook was not called
    module_hook.assert_not_called()


def test_registry_register_loaded(hooks, module_hook):
    """
    When registering a module hook
        When the module is already loaded
            We register the hook for the module name
            We immediately call the hook function
    """
    module_name = 'pytest'  # We know this must be loaded already
    hooks.register(module_name, module_hook)

    assert len(hooks.hooks) == 1
    assert module_name in hooks.hooks
    assert len(hooks.hooks[module_name]) == 1
    assert hooks.hooks[module_name] == set([module_hook])

    # Assert it was called once with the appropriate arguments
    module_hook.assert_called_once_with(pytest)


def test_registry_deregister(hooks, module_hook):
    """
    When deregistering a hook
        The hook is removed from the registry
        The hook is not called when the registry hooks call
    """
    # Register the hook
    module_name = 'test.module.name'
    hooks.register(module_name, module_hook)
    assert hooks.hooks[module_name] == set([module_hook])

    # Deregister the hook
    hooks.deregister(module_name, module_hook)

    # Ensure it was removed
    assert hooks.hooks[module_name] == set()

    # Call all hooks for the module
    # DEV: Pass in `module` so we don't try to look in `sys.modules`
    hooks.call(module_name, module=object())

    # Ensure it was not called
    module_hook.assert_not_called()


def test_registry_deregister_unknown_module(hooks, module_hook):
    """
    When deregistering a hook
        When the module is not known
            We do not raise any exceptions
    """
    # Ensure we do not have the module registered
    module_name = 'test.module.name'
    assert module_name not in hooks.hooks

    # Deregistering the hook has no side effects
    hooks.deregister(module_name, module_hook)

    # Ensure we didn't do anything weird
    assert module_name not in hooks.hooks


def test_registry_deregister_unknown_hook(hooks, module_hook):
    """
    When deregistering a hook
        When the module is not known
            We do not raise any exceptions
    """
    # Ensure we do not have the module registered
    module_name = 'test.module.name'
    hooks.register(module_name, module_hook)

    # Ensure our hook was registered
    assert hooks.hooks[module_name] == set([module_hook])

    # Deregistering a different hook
    unknown_hook = mock.Mock()
    hooks.deregister(module_name, unknown_hook)

    # Ensure we didn't remove the other hook of ours
    assert hooks.hooks[module_name] == set([module_hook])


def test_registery_reset(hooks, module_hook):
    """
    When resetting the registery
        All hooks are removed
    """
    # Register some hooks
    hooks.register('test.module.name', module_hook)
    hooks.register('pytest', module_hook)
    hooks.register('ddtrace', module_hook)
    assert len(hooks.hooks) == 3

    # Reset the registery
    hooks.reset()

    # All hooks are removed
    assert len(hooks.hooks) == 0


def test_registry_call(hooks):
    """
    When calling module hooks
        We call all module_hooks registered for the module
    """
    # Register 3 hooks for a module
    module_name = 'test.module.name'
    hook_one = mock.Mock()
    hook_two = mock.Mock()
    hook_three = mock.Mock()

    hooks.register(module_name, hook_one)
    hooks.register(module_name, hook_two)
    hooks.register(module_name, hook_three)

    try:
        # Add a module to `sys.modules` so `hooks.call` can grab it
        module = object()
        sys.modules[module_name] = module

        # Call hooks for the module
        # DEV: Pass fake module to ensure we don't grab from `sys.modules`
        hooks.call(module_name)

        # Assert all hooks were called
        hook_one.assert_called_once_with(module)
        hook_two.assert_called_once_with(module)
        hook_three.assert_called_once_with(module)
    finally:
        del sys.modules[module_name]


def test_registry_call_with_module(hooks):
    """
    When calling module hooks
        When manually passing the module in
            We call all module_hooks registered for the module
    """
    # Register 3 hooks for a module
    module_name = 'test.module.name'
    hook_one = mock.Mock()
    hook_two = mock.Mock()
    hook_three = mock.Mock()

    hooks.register(module_name, hook_one)
    hooks.register(module_name, hook_two)
    hooks.register(module_name, hook_three)

    # Call hooks for the module
    # DEV: Pass fake module to ensure we don't grab from `sys.modules`
    module = object()
    hooks.call(module_name, module)

    # Assert all hooks were called
    hook_one.assert_called_once_with(module)
    hook_two.assert_called_once_with(module)
    hook_three.assert_called_once_with(module)


def test_registry_call_with_no_module(hooks):
    """
    When calling module hooks
        When the module was not loaded
            We do not call any of the hooks
    """
    # Register 3 hooks for a module
    module_name = 'test.module.name'
    hook_one = mock.Mock()
    hook_two = mock.Mock()
    hook_three = mock.Mock()

    hooks.register(module_name, hook_one)
    hooks.register(module_name, hook_two)
    hooks.register(module_name, hook_three)

    # Call hooks for the module
    hooks.call(module_name)

    # Assert no hooks were called
    hook_one.assert_not_called()
    hook_two.assert_not_called()
    hook_three.assert_not_called()


@mock.patch('ddtrace.internal.import_hooks.log')
def test_registry_call_with_hook_exception(hooks_log, hooks):
    """
    When calling module hooks
        When the a hook raises an exception
            We do not fail
            We continue to call other hooks
    """
    # Register 3 hooks for a module
    module_name = 'test.module.name'
    hook_one = mock.Mock()
    hook_two = mock.Mock()
    hook_two.side_effect = Exception
    hook_three = mock.Mock()

    hooks.register(module_name, hook_one)
    hooks.register(module_name, hook_two)
    hooks.register(module_name, hook_three)

    # Call hooks for the module
    # DEV: Pass fake module to ensure we don't grab from `sys.modules`
    module = object()
    hooks.call(module_name, module)

    # Assert all hooks were called
    hook_one.assert_called_once_with(module)
    hook_two.assert_called_once_with(module)
    hook_three.assert_called_once_with(module)

    # Assert we logged a warning about the hook failing
    hooks_log.warning.assert_called_once_with(
        'Failed to call hook %r for module %r',
        hook_two,
        module_name,
        exc_info=True,
    )


def test_registry_call_no_name(hooks):
    """
    When calling module hooks
        When the hook is not known
            Has no side effects
    """
    # Call hooks for the module
    # DEV: Pass fake module to ensure we don't grab from `sys.modules`
    module = object()
    module_name = 'test.module.name'

    # Ensure the module isn't registered
    assert module_name not in hooks.hooks

    # Call the hooks, this should not have any side effects
    hooks.call(module_name, module)
