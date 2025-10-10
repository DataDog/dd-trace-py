"""
.. _ddtraceauto:

Importing ``ddtrace.auto`` installs Datadog instrumentation in the runtime. It should be used
when :ref:`ddtrace-run<ddtracerun>` is not an option. Using it with :ref:`ddtrace-run<ddtracerun>`
is unsupported and may lead to undefined behavior::

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


def should_import_sitecustomize():
    """
    Detect if the pytest plugin is enabled and avoid importing sitecustomize in that case.
    """
    if "pytest" not in sys.modules:
        return True

    try:
        from ddtrace.contrib.internal.pytest.plugin import is_enabled

        return not is_enabled(sys.modules["pytest"].config)
    except (ImportError, AttributeError):
        return True


if should_import_sitecustomize():
    import ddtrace.bootstrap.sitecustomize  # noqa:F401
