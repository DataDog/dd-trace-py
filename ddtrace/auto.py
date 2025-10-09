"""
.. _ddtraceauto:

Importing ``ddtrace.auto`` installs Datadog instrumentation in the runtime. It should be used
when :ref:`ddtrace-run<ddtracerun>` is not an option. Using it with :ref:`ddtrace-run<ddtracerun>`
or with the ddtrace pytest plugin is unsupported and may lead to undefined behavior::

    # myapp.py

    import ddtrace.auto  # install instrumentation as early as possible
    import mystuff

    def main():
        print("It's my app!")

    main()

If you'd like more granular control over instrumentation setup, you can call the `patch*` functions
directly.
"""
import sys


def _should_skip_auto_patch():
    """Determine if auto-patching should be skipped.
    
    Returns:
        bool: True if auto-patching should be skipped, False otherwise.
    """
    if "pytest" not in sys.modules:
        return False
        
    try:
        from ddtrace.internal.utils.pytest import is_pytest_plugin_enabled
        return is_pytest_plugin_enabled()
    except ImportError:
        return False


# Only proceed with auto-patching if we're not in a pytest environment with the plugin enabled
if not _should_skip_auto_patch():
    import ddtrace.bootstrap.sitecustomize  # noqa:F401

__all__ = []
