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

Integration Checklists
------------------------
The lists here are necessary, but not sufficient to add an integration. Please read the full document.

General (all integrations):
[ ] Includes tests (see "Testing" in this document)
[ ] The CI suite is updated to include tests for the integration

Service Naming/Peer.Service (all integrations):
[ ] The integration has (a) a default service name and (b) supports service name schema
[ ] The integration includes tests for the various service name schema (`default`, `v0`, `v1`) which are sent to the test agent
[ ] The integration CI includes the test agent tests
[ ] Operation/span names support Open Telemetry specifications for naming format and peer.service


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


Service Naming/Peer.Service
---------------------------
Service Naming and Peer.Service help with automated service detection and modeling
by providing different default values for the service and operation name span
attributes.  These attributes are adjusted by using environment variables which toggle 
various schema. Every integration is required to support the existing schema.

The API to support service name, peer.service, and schema can be found here: <https://github.com/DataDog/dd-trace-py/blob/1.x/ddtrace/internal/schema/__init__.py#L52-L64>

The important elements are:
1. Every integration needs a default service name, which is what the service name for spans will be when the integration is called.
2. The default service name should be wrapped with `schematize_service_name()`
3. If the span being created is a supported OpenTelemetry format:

  1. Wrap your operation name with the appropriate `schematize_*_operation` call (or add a new one)
  2. If OpenTelemetry specifies precursors for peer.service, ensure your span includes those as tags


The point of these changes is to allow service name schema to toggle behaviors:
* `v0`: Each integration has a default integration name, which is used in the service map and to generate APM statistics
* `v1`: Integrations now use the value of `DD_SERVICE` and the map/statistics are generated using peer.service.


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

Snapshot Tests
--------------

Many of the tests are based on "snapshots": saved copies of actual traces sent to the
`APM test agent <../README.md#use-the-apm-test-agent>`_.

To update the snapshots expected by a test, first update the library and test code to generate
new traces. Then, delete the snapshot file corresponding to your test. Use `docker-compose up -d testagent`
to start the APM test agent, and re-run the test. Use `--pass-env` as described
`here <../README.md#use-the-apm-test-agent>`_ to ensure that your test run can talk to the
test agent. Once the run finishes, the snapshot file will have been regenerated.

Writing Integration Tests for Your Integration
++++++++++++++++++++++++++++++++++++++++++++++

These instructions describe the general approach of writing new integration tests for a library integration.
They use the Flask integration tests as a teaching example. Referencing these instructions against
``tests/contrib/flask/test_flask_snapshot.py`` and ``tests/contrib/flask/app.py`` may be helpful.

1. Make sure a directory for your integration exists under ``tests/contrib``
2. Create a new file ``tests/contrib/<integration>/test_<integration>_snapshot.py``
3. Make sure a ``Venv`` instance exists in ``riotfile.py`` that references your ``contrib`` subdirectory.
   Create one if it doesn't exist. Note the name of this ``Venv`` - this is the "test suite name".
4. In this directory, write a simple "Hello World" application that uses the library you're
   integrating with similarly to how customers will use it. Depending on the library, this
   might be as simple as a function in the snapshot test file that imports the library.
   It might also be a new file in the test directory ``app.py`` as in the cases of Flask
   or Gunicorn.
5. Instrument your "hello world" app with ddtrace. In the case of Flask, this is accomplished by
   running the app server in a subprocess started with a ``ddtrace-run`` command. The app
   server is started by a Pytest fixture function that's defined in the snapshot test file.
6. If the library you're integrating with requires communication with a datastore, make sure there's
   an image for that datastore referenced in ``docker-compose.yml``. If there is not, add one.
   You can find a suitable image by searching on `Dockerhub <hub.docker.com>`_.
7. Write a simple test. In your new snapshot test file, define a function testing your app's
   happy path. Here's an example from the Flask test suite:

.. code-block:: python

    @pytest.mark.snapshot
    def test_flask_200(flask_client):
        assert flask_client.get("/", headers=DEFAULT_HEADERS).status_code == 200


This function accepts a client object, defined elsewhere in the file, as a fixture. The
client has been initialized to communicate with the server running the "hello world" app we
created in step 3. The function makes a simple request to the app server and checks the status
code.

8. Add the ``pytest.mark.snapshot`` decorator to your test function.

.. code-block:: python

    @pytest.mark.snapshot
    def test_flask_200(flask_client):
        ...


This decorator causes Pytest to collect the spans generated by your instrumented test app and compare them
against a stored set of expected spans. Since the integration test we're writing is new, there
are not yet any expected spans stored for it, so we need to create some.

9. Start the "test agent", as well as any necessary datastore containers, and run your new test:

.. code-block:: bash

   $ docker-compose up -d testagent <container>
   $ scripts/ddtest
   > DD_AGENT_PORT=9126 riot -v run --pass-env <test_suite_name>


10. Check ``git status`` and observe that some new files have been created under ``tests/snapshots/``.
    These files contain JSON representations of the spans created by the instrumentation that ran
    during your test function. Look over these spans to make sure that they're what you'd expect
    from the integration.
11. Commit the new snapshot files. The next time the snapshot test runs, it will compare the real spans
    generated by the test to these committed span JSON objects, and will fail on any differences found.
12. Test that this works: delete any attribute from one of the snapshot JSON objects, and then run the test again.
    You should observe that the test fails with a message indicating that the received and expected spans do
    not match.
13. Repeat steps 7 through 9 until you've achieved test coverage for the entire "happy path" of normal usage
    for the library you're integrating with, as well as coverage of any known likely edge cases.


Trace Examples
--------------

If in the process of writing tests for your integration you create a sample application,
consider adding it to the `trace examples repository <https://github.com/Datadog/trace-examples>`_ along
with screenshots of some example traces in the PR description.
