.. _`basic usage`:

Basic Usage
===========

Tracing
~~~~~~~

``patch_all``
-------------

To manually install the instrumentation use ``patch_all`` as early as possible
in the application::

  from ddtrace import patch_all
  patch_all()

To toggle instrumentation for a particular module::

  from ddtrace import patch_all
  patch_all(redis=False, cassandra=False)

By default all supported libraries will be instrumented when ``patch_all`` is
used.

**Note:** To ensure that the supported libraries are instrumented properly in
the application, they must be patched *prior* to being imported. So make sure
to call ``patch_all`` *before* importing libraries that are to be instrumented.

More information about ``patch_all`` is available in the :ref:`patch_all` API
documentation.


Manual Instrumentation
----------------------

To extend the functionality of the ``ddtrace`` library several APIs are
provided.

Decorator
---------

``ddtrace`` provides a decorator that can be used to trace a particular method
in your application::

  @tracer.wrap()
  def business_logic():
    """A method that would be of interest to trace."""
    # ...
    # ...

API documentation can be found here :py:meth:`ddtrace.Tracer.wrap`.

Context Manager
---------------

To trace an arbitrary block of code, you can use :py:meth:`ddtrace.Tracer.trace`
that returns a :py:mod:`ddtrace.Span` which can be used as a context manager::

  # trace some interesting operation
  with tracer.trace('interesting.operations'):
    # do some interesting operation(s)
    # ...
    # ...

API documentation can be found here :py:meth:`ddtrace.Tracer`.

Using the API
-------------

If the above methods are still not enough to satisfy your tracing needs, a
manual API to provide complete control over starting and stopping spans.

  span = tracer.trace('operations.of.interest')  # span is started once created

  # do some operation(s) of interest in between

  # NOTE: be sure to call span.finish() or the trace will not be sent to
  #       Datadog
  span.finish()

API details for creating and finishing spans can be found here:

- :py:meth:`ddtrace.Tracer.trace`
- :py:meth:`ddtrace.Span.finish`.


Profiling
~~~~~~~~~

Via module
----------
To automatically profile your code, you can import the `ddtrace.profiling.auto` module.
As soon as it is imported, it will start capturing CPU profiling information on
your behalf::

  import ddtrace.profiling.auto

Via API
-------
If you want to control which part of your code should be profiled, you can use
the `ddtrace.profiling.Profiler` object::

  from ddtrace.profiling import Profiler

  prof = Profiler()
  prof.start()

  # At shutdown
  prof.stop()

.. important::

   The profiler has been designed to be always-on. The ``start`` and ``stop``
   methods are provided in case you need a fine-grained control over the
   profiler lifecycle. They are not provided for starting and stopping the
   profiler many times during your application lifecycle. Do not use them for
   e.g. building a context manager.


Handling `os.fork`
------------------

When your process forks using `os.fork`, the profiler is stopped in the child
process.

For Python 3.7 and later on POSIX platforms, a new profiler will be started if
you enabled the profiler via `ddtrace-run` or `ddtrace.profiling.auto`.

If you manually instrument the profiler, or if you rely on Python 3.6 or a
non-POSIX platform and earlier version, you'll have to manually restart the
profiler in your child.

The global profiler instrumented by `ddtrace-run` and `ddtrace.profiling.auto`
can be started by calling `ddtrace.profiling.auto.start_profiler`.
