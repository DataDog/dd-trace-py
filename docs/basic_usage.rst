.. _`basic usage`:

Basic Usage
===========

Automatic Instrumentation
~~~~~~~~~~~~~~~~~~~~~~~~~

``ddtrace.auto``
----------------

To enable full ddtrace support (library instrumentation, profiling, application security monitoring, dynamic instrumentation, etc.) call :ref:`import ddtrace.auto<ddtraceauto>` as the very first thing
in your application. This will only work if your application is not running under ``ddtrace-run``.

Note: Some Datadog products and instrumentation are disabled by default. Products and instrumentation can be enabled/disable via environment variables, see :ref:`configurations <Configuration>` page for more details.

Tracing
~~~~~~~

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

API documentation can be found here :py:meth:`ddtrace.trace.Tracer.wrap`.

Context Manager
---------------

To trace an arbitrary block of code, you can use :py:meth:`ddtrace.trace.Tracer.trace`
that returns a :py:mod:`ddtrace.trace.Span` which can be used as a context manager::

  # trace some interesting operation
  with tracer.trace('interesting.operations'):
    # do some interesting operation(s)
    # ...
    # ...

API documentation can be found here :py:meth:`ddtrace.trace.Tracer`.

Using the API
-------------

If the above methods are still not enough to satisfy your tracing needs, a
manual API to provide complete control over starting and stopping spans is available::

  span = tracer.trace('operations.of.interest')  # span is started once created

  # do some operation(s) of interest in between

  # NOTE: be sure to call span.finish() or the trace will not be sent to
  #       Datadog
  span.finish()

API details for creating and finishing spans can be found here:

- :py:meth:`ddtrace.trace.Tracer.trace`
- :py:meth:`ddtrace.trace.Span.finish`.


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


Asyncio Support
---------------

The profiler supports the ``asyncio`` library and retrieves the
``asyncio.Task`` names to tag along the profiled data.

For this to work, the profiler `replaces the default event loop policy
<https://docs.python.org/3/library/asyncio-policy.html#asyncio-policies>`_ with
a custom policy that tracks threads to loop mapping.

The custom asyncio loop policy is installed by default at profiler startup. You
can disable this behavior by using the ``asyncio_loop_policy`` parameter and
passing it ``None``::

  from ddtrace.profiling import Profiler

  prof = Profiler(asyncio_loop_policy=None)

You can also pass a custom class that implements the interface from
``ddtrace.profiling.profiler.DdtraceProfilerEventLoopPolicy``::


  from ddtrace.profiling import Profiler

  prof = Profiler(asyncio_loop_policy=MyLoopPolicy())


If the loop policy has been overridden after the profiler has started, you can
always restore the profiler asyncio loop policy by calling
the ``set_asyncio_event_loop_policy`` method::

  from ddtrace.profiling import Profiler

  prof = Profiler()
  prof.set_asyncio_event_loop_policy()
