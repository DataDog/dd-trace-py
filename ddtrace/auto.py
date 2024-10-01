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
import ddtrace.bootstrap.sitecustomize  # noqa:F401
from ddtrace.internal.telemetry import telemetry_writer


telemetry_writer.add_configuration("ddtrace_auto_used", "True", "unknown")
