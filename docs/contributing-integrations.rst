.. _integration_guidelines:

What's an Integration?
----------------------

An integration is code that builds a tree of ``ExecutionContext`` objects representing the
runtime state of a given third-party Python module and emits events indicating interesting moments
during that runtime. It's implemented as a module in ``ddtrace.contrib``.

There's a skeleton module in ``templates/integration`` that can serve as a helpful starting point
for new integrations. You can copy it to ``contrib`` and replace ``foo`` with the name of the library you're
integrating with::

      cp -r templates/integration ddtrace/contrib/<integration>

Integrations must avoid changing the contract between the application and the integrated library. That is, they
should be completely invisible to the application code. This means integration code should, for example,
re-raise exceptions after catching them.

Integrations shouldn't include any code that references concepts that are specific to Datadog Products. Examples
include Tracing Spans and the AppSec WAF.

What tools does an integration rely on?
---------------------------------------

Integrations rely primarily on Wrapt, the Pin API, and the Core API.

The `Wrapt <https://pypi.org/project/wrapt/>`_ library, vendored in ``ddtrace.vendor.wrapt``, is the main
piece of code that ``ddtrace`` uses to hook into the runtime execution of third-party libraries. The essential
task of writing an integration is determining the functions in the third-party library that would serve as
useful entrypoints and wrapping them with ``wrap_function_wrapper``. There are exceptions, but this is
generally a useful starting point.

The Pin API in ``ddtrace.pin`` is used to configure the instrumentation at runtime. It provides a ``Pin`` class
that can store configuration data in memory in a manner that is accessible from within functions wrapped by Wrapt.
``Pin`` objects are most often used for storing configuration data scoped to a given integration, such as
enable/disable flags and service name overrides.

The Core API in ``ddtrace.internal.core`` is the abstraction layer between the integration code and code for
Products. The integration builds and maintains a tree of ``ExecutionContext`` objects representing the state
of the library's execution by calling ``core.context_with_data``. The integration also emits events indicating
interesting occurrences in the library at runtime via ``core.dispatch``. This approach means that integrations
do not need to include any code that references Products, like knowing about Spans or the WAF.

The combination of these three tools lets integration code provide richly configured execution data to Product
code while maintaining useful encapsulation.


What versions of a given library should an integration support?
---------------------------------------------------------------

``ddtrace`` supports versions of integrated libraries according to these guidelines:

  - Test the earliest and latest minor versions of the latest major version.

  - Test the latest minor version of all non-latest major versions going back 2 years.

  - If there are no new releases in the past 2 years, test the latest released version.

For libraries with many versions it is recommended to pull out the version of
the library to use when instrumenting volatile features. A great example of
this is the Flask integration:

    - `pulling out the version: <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/flask/patch.py#L45-L58>`_
    - `using it to instrument a later-added feature <https://github.com/DataDog/dd-trace-py/blob/96dc6403e329da87fe40a1e912ce72f2b452d65c/ddtrace/contrib/flask/patch.py#L149-L151>`_


Are there norms for integrating with web frameworks?
----------------------------------------------------

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

Are there norms for integrating with database libraries?
--------------------------------------------------------

``ddtrace`` instruments Python PEP 249 database API, which most database client libraries implement, in the
`ddtrace.contrib.dbapi <https://github.com/DataDog/dd-trace-py/blob/46a2600/ddtrace/contrib/dbapi/__init__.py>`_
module.

Check out existing database integrations for examples of using the `dbapi`:

    - `mariadb <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/mariadb>`_
    - `psycopg <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/psycopg>`_
    - `mysql <https://github.com/DataDog/dd-trace-py/tree/46a2600/ddtrace/contrib/mysql>`_

How should an integration be tested?
------------------------------------

The tests for your integration should be defined in their own module at ``tests/contrib/<integration>/``.

Testing is the most important part of the integration. We have to be certain
that the integration submits meaningful information to Datadog and does not
impact the library or application by disturbing state, performance or causing errors. The integration
should be invisible to users.

What are "snapshot tests"?
--------------------------

Many of the tests are based on "snapshots": saved copies of actual traces sent to the
`APM test agent <../README.md#use-the-apm-test-agent>`_. When an integration is added or modified, the snapshots
(if they exist) should be updated to match the new expected output.

1. Update the library and test code to generate new traces.
2. Delete the snapshot file corresponding to your test at ``tests/snapshots/<snapshot_file>`` (if applicable).
3. Use `docker-compose up -d testagent` to start the APM test agent, and then re-run the test. Use `--pass-env` as described
   `here <../README.md#use-the-apm-test-agent>`_ to ensure that your test run can talk to the test agent. 

Once the run finishes, the snapshot file will have been regenerated.

How should I write integration tests for my integration?
--------------------------------------------------------

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
14. Enable the `snapshot` option in `.circleci/config.templ.yml` and run the test as a `machine_executor` at ``.circleci/config.templ.yml``
    just like:

.. code-block:: yaml

  <test_suite_name>:
    <<: *machine_executor
    steps:
      - run_test:
          pattern: '<test_suite_name>'
          snapshot: true


If in the process of writing tests for your integration you create a sample application,
consider adding it to the `trace examples repository <https://github.com/Datadog/trace-examples>`_ along
with screenshots of some example traces in the PR description.

What does a complete PR look like when adding a new integration?
----------------------------------------------------------------

The following is the check list for ensuring you have all of the components to have a complete PR that is ready for review.

- Patch code for your new integration under ``ddtrace/contrib/your_integration_name``.
- Test code for the above in ``tests/contrib/your_integration_name``.
- The virtual environment configurations for your tests in ``riotfile.py``.
- The Circle CI configurations for your tests in ``.circleci/config.templ.yml``.
- Your integration added to ``PATCH_MODULES`` in ``ddtrace/_monkey.py`` to enable auto instrumentation for it.
- The relevant file paths for your integration added to ``tests/.suitespec.json`` in two locations:
    - Add non-test file paths under ``components``.
    - Add test file paths under ``suites``.
- A release note for your addition generated with ``riot run reno new YOUR_TITLE_SLUG``, which will add ``releasenotes/notes/YOUR_TITLE_SLUG.yml``.