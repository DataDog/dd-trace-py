Basic Usage
===========

With ``ddtrace`` installed, the application can be instrumented.


Auto Instrumentation
--------------------

Python applications can easily be instrumented with ``ddtrace`` by using the
included ``ddtrace-run`` command. Simply prefix the Python execution command
with ``ddtrace-run`` in order to auto-instrument the libraries in the
application.

For example, if the command to the application run is::

$ python app.py

then to enable tracing, the corresponding command is::

$ ddtrace-run python app.py


``ddtrace-run``
^^^^^^^^^^^^^^^
Datadog tracing can automatically instrument many widely used Python libraries
and frameworks.

Once installed, the package will make the ``ddtrace-run`` command-line entrypoint
available in your Python environment.

``ddtrace-run`` will trace available web frameworks and database modules without
the need for changing your code::

  $ ddtrace-run -h

  Execute the given Python program, after configuring it
  to emit Datadog traces.

  Append command line arguments to your program as usual.

  Usage: [ENV_VARS] ddtrace-run <my_program>


The available environment variables for ``ddtrace-run`` are:

* ``DATADOG_TRACE_ENABLED=true|false`` (default: true): Enable web framework and
  library instrumentation. When false, your application code will not generate
  any traces.
* ``DATADOG_ENV`` (no default): Set an application's environment e.g. ``prod``,
  ``pre-prod``, ``stage``
* ``DATADOG_TRACE_DEBUG=true|false`` (default: false): Enable debug logging in
  the tracer
* ``DATADOG_SERVICE_NAME`` (no default): override the service name to be used
  for this program. This value is passed through when setting up middleware for
  web framework integrations (e.g. pylons, flask, django). For tracing without a
  web integration, prefer setting the service name in code.
* ``DATADOG_PATCH_MODULES=module:patch,module:patch...`` e.g.
  ``boto:true,redis:false``: override the modules patched for this execution of
  the program (default: none)
* ``DATADOG_TRACE_AGENT_HOSTNAME=localhost``: override the address of the trace
  agent host that the default tracer will attempt to submit to  (default:
  ``localhost``)
* ``DATADOG_TRACE_AGENT_PORT=8126``: override the port that the default tracer
  will submit to  (default: 8126)
* ``DATADOG_PRIORITY_SAMPLING`` (default: false): enables `Priority sampling`_

``ddtrace-run`` respects a variety of common entrypoints for web applications:

- ``ddtrace-run python my_app.py``
- ``ddtrace-run python manage.py runserver``
- ``ddtrace-run gunicorn myapp.wsgi:application``
- ``ddtrace-run uwsgi --http :9090 --wsgi-file my_app.py``


Pass along command-line arguments as your program would normally expect them::

    ddtrace-run gunicorn myapp.wsgi:application --max-requests 1000 --statsd-host localhost:8125

*As long as your application isn't running in* ``DEBUG`` *mode, this should be
enough to see your application traces in Datadog.*

If you're running in a Kubernetes cluster, and still don't see your traces, make
sure your application has a route to the tracing Agent. An easy way to test this
is with a::


$ pip install ipython
$ DATADOG_TRACE_DEBUG=true ddtrace-run ipython

Because iPython uses SQLite, it will be automatically instrumented, and your
traces should be sent off. If there's an error, you'll see the message in the
console, and can make changes as needed.

Please read on if you are curious about further configuration, or
would rather set up Datadog Tracing explicitly in code.


Manual Instrumentation
----------------------

Manually instrumenting a Python application is as easy as::

  from ddtrace import patch_all
  patch_all()

**Note:** To ensure that the supported libraries are instrumented properly in
the application, they must be patched *prior* to importing them. So make sure to
call ``patch_all`` *before* importing libraries that are to be instrumented!

More information about ``patch_all`` is available in our `patch_all API
documentation`_.


Decorator
^^^^^^^^^


Context Manager
^^^^^^^^^^^^^^^


Completely Manual
^^^^^^^^^^^^^^^^^

