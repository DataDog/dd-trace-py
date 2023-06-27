===========================
How To Write an Integration
===========================

An integration should provide concise, insightful data about the library or
framework that will aid developers in monitoring their application's health and
performance.

The best way to get started writing a new integration is to refer to existing
integrations. Looking at the integration for a similar library or framework is a great
starting point. To write a new integration for ``memcached``, for example, we might
refer to the existing ``redis`` integration as a starting point since both are
datastores that focus on in-memory storage.

Your development process might look like this:

  - Research the library or framework that you're instrumenting. Reading
    through its docs and code examples will reveal what APIs are meaningful to
    instrument.

  - Copy the skeleton module provided in ``templates/integration`` and replace
    ``foo`` with the integration name. The integration name typically matches
    the library or framework being instrumented::

      cp -r templates/integration ddtrace/contrib/<integration>

  - Create a test file for the integration under
    ``tests/contrib/<integration>/test_<integration>.py``.

  - Write the integration (see more on this below).

  - Open a `pull request <contributing.rst#change-process>`_ containing your changes.

All integrations live in ``ddtrace/contrib/`` and contain at least two files,
``__init__.py`` and ``patch.py``. A skeleton integration is available under
``templates/integration`` which can be used as a starting point::

    cp -r templates/integration ddtrace/contrib/<integration>

The Pin API in ``ddtrace.pin`` is used to configure the instrumentation at runtime, including
enabling and disabling the instrumentation and overriding the service name.

Library support
---------------

``ddtrace`` supports versions of integrated libraries according to these guidelines:

  - Test the oldest and latest minor versions of the most latest major version going back 2 years.

  - Test the latest minor version of any previous major version going back 2 years.

  - If there are no new releases in the past 2 years, test the latest released version.

  - For legacy Python versions (2.7,3.5,3.6), test the latest minor version known to support that legacy Python version.

For libraries with many versions it is recommended to pull out the version of
the library to use when instrumenting volatile features. A great example of
this is the Flask integration:

    - `pulling out the version: <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/flask/patch.py#L45-L58>`_
    - `using it to instrument a later-added feature <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/flask/patch.py#L149-L151>`_


Exceptions and Errors
---------------------

Exceptions provide a lot of useful information and are usually easy to deal with. Exceptions are
a great place to start instrumenting. There are a couple of considerations when
dealing with exceptions in ``ddtrace``.

Re-raise exceptions when your integration code catches them, because it is crucial that ddtrace does not
change the contract between the application and the integrated library. See the
`bottle exception handling <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/bottle/trace.py#L50-L69>`_
instrumentation for an example.

Gather relevant information from exceptions. ``ddtrace`` provides a helper for pulling
out common exception data and adding it to a span. See the
`cassandra exception handling <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/cassandra/session.py#L117-L122>`_
instrumentation for an example.

Tracing across execution boundaries
-----------------------------------

Some integrations need to propagate traces across execution boundaries, to other threads,
processes, tasks, or other units of execution. For example, here's how the `requests integration <https://github.com/DataDog/dd-trace-py/blob/46a2600/ddtrace/contrib/requests/connection.py#L95-L97>`_
handles this, and here's the `django integration <https://github.com/DataDog/dd-trace-py/blob/46a2600/ddtrace/contrib/django/patch.py#L304>`_'s
implementation.

Web frameworks
--------------

A web framework integration should:

    - Install the WSGI or ASGI trace middlewares already provided by ``ddtrace``
    - Emit a trace covering the entire duration of a request
    - Assign one resource name per application "route" (a "route" is a URL pattern on which the application listens for requests)
    - Use ``trace_utils.set_http_meta`` to set the standard http tags
    - Set an internal service name
    - Support configurable distributed tracing
    - Provide insight to middlewares and views, if applicable
    - Use the `SpanTypes.WEB` span type

Some example web framework integrations::
    - `flask <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/flask>`_
    - `django <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/django>`__

Database libraries
------------------

``ddtrace`` already provides base instrumentation for the Python database API
(PEP 249) which most database client libraries implement in the
`ddtrace.contrib.dbapi <https://github.com/DataDog/dd-trace-py/blob/46a2600/ddtrace/contrib/dbapi/__init__.py>`_
module.

Check out some of our existing database integrations for how to use the `dbapi`:

    - `mariadb <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/mariadb>`_
    - `psycopg <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/psycopg>`_
    - `mysql <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/mysql>`_

Testing
-------

The tests for your integration should be defined in their own module at ``tests/contrib/<integration>/``.

Testing is the most important part of the integration. We have to be certain
that the integration submits meaningful information to Datadog and does not
impact the library or application by disturbing state, performance or causing errors. The integration
should be invisible to users.

The best way to get started writing tests is to reference other integration test
suites. ``tests/contrib/django`` and ``tests/contrib/mariadb`` are good examples.
Be sure to make use of preexisting test utilities and fixtures where applicable.

Snapshot Tests
--------------

Many of the tests are based on "snapshots": saved copies of actual traces sent to the
`APM test agent <../README.md#use-the-apm-test-agent>`_.

To update the snapshots expected by a test, first update the library and test code to generate
new traces. Then, delete the snapshot file corresponding to your test. Use `docker-compose up -d testagent`
to start the APM test agent, and re-run the test. Use `--pass-env` as described
`here <../README.md#use-the-apm-test-agent>`_ to ensure that your test run can talk to the
test agent. Once the run finishes, the snapshot file will have been regenerated.

Trace Examples
--------------

If in the process of writing tests for your integration you create a sample application,
consider adding it to the `trace examples repository <https://github.com/Datadog/trace-examples>`_ along
with screenshots of some example traces in the PR description.
