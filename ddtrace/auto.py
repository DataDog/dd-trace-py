"""
ddtrace.auto
============

Importing this module installs Datadog instrumentation in the runtime. It should be used
when :ref:`ddtrace-run<ddtrace-run>` is not an option. Using it with :ref:`ddtrace-run<ddtrace-run>`
is unsupported and may lead to undefined behavior::

    # myapp.py

    import ddtrace.auto  # install instrumentation as early as possible
    import mystuff

    def main():
        print("It's my app!")

    main()
"""
import ddtrace.bootstrap.sitecustomize  # noqa
