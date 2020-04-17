.. _`basic usage`:

Basic Usage
===========

Tracer
~~~~~~

With ``ddtrace`` installed, the application can be instrumented.


Auto Instrumentation
--------------------

``ddtrace-run``
^^^^^^^^^^^^^^^

Python applications can easily be instrumented with ``ddtrace`` by using the
included ``ddtrace-run`` command. Simply prefix your Python execution command
with ``ddtrace-run`` in order to auto-instrument the libraries in your
application.

For example, if the command to run your application is::

$ python app.py

then to auto-instrument using Datadog, the corresponding command is::

$ ddtrace-run python app.py

For more advanced usage of ``ddtrace-run`` refer to the documentation :ref:`here<ddtracerun>`.

``patch_all``
^^^^^^^^^^^^^

To manually invoke the automatic instrumentation use ``patch_all``::

  from ddtrace import patch_all
  patch_all()

To toggle instrumentation for a particular module::

  from ddtrace import patch_all
  patch_all(redis=False, cassandra=False)

By default all supported libraries will be patched when
``patch_all`` is invoked.

**Note:** To ensure that the supported libraries are instrumented properly in
the application, they must be patched *prior* to being imported. So make sure
to call ``patch_all`` *before* importing libraries that are to be instrumented.

More information about ``patch_all`` is available in our :ref:`patch_all` API
documentation.


Manual Instrumentation
----------------------

If you would like to extend the functionality of the ``ddtrace`` library or gain
finer control over instrumenting your application, several techniques are
provided by the library.

Decorator
^^^^^^^^^

``ddtrace`` provides a decorator that can be used to trace a particular method
in your application::

  @tracer.wrap()
  def business_logic():
    """A method that would be of interest to trace."""
    # ...
    # ...

API details of the decorator can be found here :py:meth:`ddtrace.Tracer.wrap`.

Context Manager
^^^^^^^^^^^^^^^

To trace an arbitrary block of code, you can use :py:meth:`ddtrace.Tracer.trace`
that returns a :py:mod:`ddtrace.Span` which can be used as a context manager::

  # trace some interesting operation
  with tracer.trace('interesting.operations'):
    # do some interesting operation(s)
    # ...
    # ...

Further API details can be found here :py:meth:`ddtrace.Tracer`.

Using the API
^^^^^^^^^^^^^

If the above methods are still not enough to satisfy your tracing needs, a
manual API is provided which will allow you to start and finish spans however
you may require::

  span = tracer.trace('operations.of.interest')

  # do some operation(s) of interest in between

  # NOTE: make sure to call span.finish() or the entire trace will not be sent
  # to Datadog
  span.finish()

API details of the decorator can be found here:

- :py:meth:`ddtrace.Tracer.trace`
- :py:meth:`ddtrace.Span.finish`.


Profiler
~~~~~~~~

.. note::

  Note that this library does not use the `Datadog agent
  <https://docs.datadoghq.com/agent/>`_. The profiles are directly sent over
  HTTP to Datadog's API.

  Therefore, in order to use the profiler and export the profiles to Datadog,
  you'll need to at least set ``DD_API_KEY`` in your application environment.
  See :ref:`Configuration` for more details.

Via module
----------
To automatically profile your code, you can import the `ddtrace.profiling.auto` module.
As soon as it is imported, it will start catching CPU profiling information on
your behalf::

  import ddtrace.profiling.auto

Via API
----------
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
   e.g. buildin a context manager.

Via command line
----------------
You can run your program with profiling enabled by using the wrapper
`pyddprofile`. This will automatically enable the profiling of your
application::

  $ pyddprofile myscript.py


Handling `os.fork`
------------------

When your process forks using `os.fork`, the profiler is stopped in the child
process.

For Python 3.7 and later on POSIX platforms, a new profiler will be started if
you enabled the profiler via `pyddprofile` or `ddtrace.profiling.auto`.

If you manually instrument the profiler, or if you rely on Python 3.6 or a
non-POSIX platform and earlier version, you'll have to manually restart the
profiler in your child.

The global profiler instrumented by `pyddprofile` and `ddtrace.profiling.auto`
can be started by calling `ddtrace.profiling.auto.start_profiler`.
