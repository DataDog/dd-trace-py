.. _integration_guidelines:

What's an Integration?
----------------------

An integration is code that builds a tree of ``ExecutionContext`` objects representing the
runtime state of a given third-party Python module and emits events indicating interesting moments
during that runtime. It's implemented as a module in ``ddtrace.contrib``.

There's a skeleton module in ``templates/integration`` that can serve as a helpful starting point
for new integrations. You can copy it to ``contrib`` and replace ``foo`` with the name of the library you're
integrating with::

      cp -r templates/integration ddtrace/contrib/internal/<integration>

Integrations must avoid changing the contract between the application and the integrated library. That is, they
should be completely invisible to the application code. This means integration code should, for example,
re-raise exceptions after catching them.

Integrations shouldn't include any code that references concepts that are specific to Datadog Products. Examples
include Tracing Spans and the AppSec WAF.

Integrations should avoid exposing a public API unless it is absolutely necessary. Users should be able to configure
the integration by setting environment variables. For cases where a public API is necessary, integrations
should expose the API in ``ddtrace.contrib.<integration_name>.py``.

Integrations should define a ``ddtrace.contrib.internal.<integration>.__init__.py`` module that contains a doc string describing the integration
and it's supported configurations. This module should be referenced in the ``docs/integrations.rst`` file.

What tools does an integration rely on?
---------------------------------------

Integrations rely primarily on Wrapt, the Pin API, and the Core API.

The `Wrapt <https://pypi.org/project/wrapt/>`_ library is the main piece of code that ``ddtrace`` uses to hook
into the runtime execution of third-party libraries. The essential task of writing an integration is determining
the functions in the third-party library that would serve as useful entrypoints and wrapping them with ``wrap_function_wrapper``.
There are exceptions, but this is generally a useful starting point.

The Pin API in ``ddtrace._trace.pin.Pin`` is used to configure the instrumentation at runtime. It provides a ``Pin`` class
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

Synchronous web request execution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``ddtrace.contrib.internal.web_framework.run_web_framework_request`` brackets a
synchronous callback with metadata hooks and a typed request context. It does not
import a web framework, inspect responses, make blocking decisions, or manage spans.
``TracingSubscriber`` and ``WebFrameworkRequestSubscriber`` own propagation, span
creation, HTTP tagging, error recording, and span completion.

``prepare()`` returns a new ``WebFrameworkRequestEvent`` before context entry. It
supplies request fields and integration-specific tracing inputs such as the
operation, service, and route. ``complete(event, result, error)`` runs inside the
context after the callback returns or raises, before HTTP tags are applied. The
error is the original exception, or ``None`` on success; the result is ``None``
when the callback raised. Hooks update event fields without accessing spans.
They must not consume response bodies or retain exceptions and tracebacks.

For an integration that needs response inspection or replacement, the optional
``execute(event, invoke)`` adapter runs inside the context. Calling ``invoke()``
executes the application callback with its original arguments and completes its
metadata before returning or re-raising. The adapter can then inspect the metadata
and preserve or replace the outcome. If it skips the callback or replaces its
outcome, the adapter must update the final metadata before returning; it can use
``complete_request_safely(complete, event, result, error)``. The adapter must not
invoke the application more than once or manage context or span completion.
Without an adapter, the runner calls ``invoke()`` directly.

Request hooks and context events run regardless of tracing enablement. An ordinary
preparation error bypasses the context and adapter and invokes the application once.
An ordinary completion error preserves the application's result or exception.
Process-control exceptions from metadata hooks are not suppressed.

The default context route is ``web.request``. An integration can provide
``context_name_override`` when its subscribers use a specialized route; the tracing
subscriber must also listen to that route. Completion is at the synchronous callback
boundary. Returned iterables are passed through unchanged. Asynchronous handlers
and deferred completion require a different lifecycle runner.

Bottle request adapter
^^^^^^^^^^^^^^^^^^^^^^

Bottle's plugin supplies ``prepare`` and ``complete`` hooks and an execution adapter
to the shared runner, using ``context_name_override="bottle.request"``. Bottle
continues to manage plugin ordering, skip settings, installation, and callback-cache
resets. The adapter interprets Bottle responses and closes discarded response bodies;
these details stay outside the shared runner.

The adapter dispatches ``BottleRequestStartEvent`` before invoking the route callback
and ``BottleResponseEvent`` after metadata completion, while the response can still
be replaced. These notifications use ``core.dispatch_event``. AppSec subscribers own
request evaluation, blocking configuration, and registration of the public
``block_request()`` capability. They inspect request headers, URI, query parameters,
cookies, path parameters, and callback response status and headers without consuming
request or response bodies.

The adapter catches ``BlockingException`` inside the request context, including
blocks raised before the callback and blocks nested in exception groups. A
``BottleBlockResponseEvent`` subscriber supplies the selected status, headers, and
body on the event. Bottle constructs an ``HTTPResponse`` and completes the replacement
metadata before returning to the shared runner. Discarded response iterables are
closed without iteration. A pending block is checked again at response inspection,
including when application code catches the blocking exception. AppSec cleanup uses
a ``ContextSubscriber`` for ``bottle.request``.

These notifications run independently of APM tracing enablement. AppSec subscribers
check their own enablement. Blocking is limited to callbacks covered by the plugin;
unmatched routes, skipped routes, earlier framework hooks, later response hooks,
and streaming after callback return remain outside this boundary.



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
`APM test agent <https://github.com/datadog/dd-apm-test-agent?tab=readme-ov-file#datadog-apm-test-agent>`_. When an integration is added or modified, the snapshots
(if they exist) should be updated to match the new expected output.

1. Update the library and test code to generate new traces.
2. Delete the snapshot file corresponding to your test at ``tests/snapshots/<snapshot_file>`` (if applicable).
3. Re-run the test with ``scripts/run-tests`` so the APM test agent and its URL are configured automatically.

Once the run finishes, the snapshot file will have been regenerated.

Snapshots of OTLP traces
~~~~~~~~~~~~~~~~~~~~~~~~

The test agent also snapshots the OTLP traces it receives during a test session to
``tests/snapshots/<token>_otlp_traces.json``, next to the native ``<token>.json`` snapshot. The file is an OTLP/JSON
document, which is what a collector receives over ``http/json``: ids are renumbered and the snapshot ignores reuse the
native syntax, for example ``meta.<key>`` matches the span and resource attribute ``<key>``.

To compare Datadog and OpenTelemetry semantics, export OTLP from a subprocess test and parametrize
``DD_TRACE_OTEL_SEMANTICS_ENABLED`` so each variant writes its own file. The W3C ``tracestate`` carries the random parent id, so ignore it::

    @pytest.mark.snapshot(ignores=["meta.tracestate"])
    @pytest.mark.subprocess(
        env={"OTEL_TRACES_EXPORTER": "otlp", "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf"},
        parametrize={"DD_TRACE_OTEL_SEMANTICS_ENABLED": ["false", "true"]},
    )
    def test_my_integration():
        ...

The tracer sends the test session token with its OTLP requests, and ``snapshot_context`` points the OTLP exporter at
the test agent when ``scripts/run-tests`` remaps its OTLP port. See ``tests/integration/test_otlp_trace_snapshot.py``
for an example of a test that exports OTLP.

How should I write integration tests for my integration?
--------------------------------------------------------

These instructions describe the general approach of writing new integration tests for a library integration.
They use the Flask integration tests as a teaching example. Referencing these instructions against
``tests/contrib/flask/test_flask_snapshot.py`` and ``tests/contrib/flask/app.py`` may be helpful.

1. Make sure a directory for your integration exists under ``tests/contrib``
2. Create a new file ``tests/contrib/<integration>/test_<integration>_snapshot.py``
3. Make sure a test environment exists for your ``contrib`` subdirectory. Follow a nearby integration and note its
   test suite name.
4. In this directory, write a simple "Hello World" application that uses the library you're
   integrating with similarly to how customers will use it. Depending on the library, this
   might be as simple as a function in the snapshot test file that imports the library.
   It might also be a new file in the test directory ``app.py`` as in the cases of Flask
   or Gunicorn.
5. Instrument your "hello world" app with ddtrace. In the case of Flask, this is accomplished by
   running the app server in a subprocess started with a ``ddtrace-run`` command. The app
   server is started by a Pytest fixture function that's defined in the snapshot test file.
6. If the library you're integrating with requires communication with a datastore, make sure there's
   an image for that datastore referenced in ``docker-compose.base.yml``. If there is not, add one.
   You can find a suitable image by searching on `Docker Hub <https://hub.docker.com>`_.
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

9. Run your new test. The test runner starts the test agent and any declared services:

.. code-block:: bash

   $ scripts/run-tests tests/contrib/<test_suite_name>

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
14. Add a component to `tests/contrib/suitespec.yml` for your integration. The component name should match
    your integration name and list all related files. This helps CI run only relevant tests when files change.

    Example:

.. code-block:: yaml

    mongo:
    - ddtrace/contrib/internal/pymongo/*
    - ddtrace/ext/mongo.py

15. Add a `suite` for your integration in `tests/contrib/suitespec.yml`. This defines test configuration
    including which docker services to run and file paths to monitor. Set `snapshot: true` for snapshot tests
    to enable the test agent.

    Example:

.. code-block:: yaml

    asyncpg:
      venvs_per_job: 5
      paths:
        - '@bootstrap'
        - '@core'
        - '@contrib'
        - '@tracing'
        - '@pg'
        - tests/contrib/asyncpg/*
        - tests/snapshots/tests.{suite}.*
        - tests/contrib/shared_tests_async.py
      snapshot: true
      services:
        - postgres

``venvs_per_job`` is the target number of dependency environments assigned to each generated CI job.
Lower values create more jobs. By default, it is the suite's total environment count, so omitting it runs
the suite as one job. The generator limits each suite to 25 jobs. Do not set ``parallelism`` directly.
Suites using ``ddtest: true`` use ``ddtest_nodes`` instead and must not set ``venvs_per_job``.

If in the process of writing tests for your integration you create a sample application,
consider adding it to the `trace examples repository <https://github.com/Datadog/trace-examples>`_ along
with screenshots of some example traces in the PR description.

What is an integration manifest?
--------------------------------

Every integration ships a manifest at ``ddtrace/contrib/internal/<integration>/<integration>.manifest.yaml``.
It is declarative, machine-readable metadata describing what the integration instruments, so that tools
listing Datadog integrations do not have to parse tracer code. It has no effect at runtime and is not
included in the published package.

Here is the ``aiokafka`` manifest:

.. code-block:: yaml

    _v: 1
    name: aiokafka
    display_name: aiokafka
    description: Instruments aiokafka message publishing and consumption with distributed tracing and Data Streams Monitoring,
      including consumer offset tracking.
    packages:
    - name: aiokafka
    category:
    - messaging.consumer
    - messaging.producer
    systems:
    - kafka
    creation_date: '2025-11-18'

Keep the fields in this order:

``_v``
    The manifest schema version. Always ``1``.

``name``
    The integration name. It must match the directory name and the ``integration_name`` in
    ``scripts/integration_registry/registry.yaml``. A new integration that registers settings with
    ``config._add()`` should use the same name there. Some existing integrations use a different configuration
    key, for example ``sqlite3`` registers ``sqlite``; the manifest still uses the directory name.

``display_name``
    The library's established spelling and capitalization, for example ``Flask``, ``HTTPX``, or ``LangGraph``.
    When the integration is named after a protocol or technology rather than a package, use that technology's
    spelling, for example ``GraphQL``, ``gRPC``, or ``Kafka``.

``description``
    One sentence, normally starting with "Instruments" and usually 15 to 25 words, describing the behavior the
    integration instruments. Describe each distinct operation family once, for example "message publishing and
    consumption", instead of listing individual wrapped methods. Describe only what the patch code covers, not
    everything the library can do.

``packages``
    The PyPI distributions that activate the integration, as a sorted list of ``- name: <distribution>`` entries.
    These must match ``dependency_names`` in ``scripts/integration_registry/registry.yaml``. Use ``[]`` for
    standard library integrations such as ``httplib`` or ``logging``.

``category``
    A sorted, non-empty list of every kind of operation the integration instruments, such as ``db.client``,
    ``http.client``, ``http.server``, ``messaging.consumer``, ``messaging.producer``, ``gen_ai``, or
    ``template.render``. Each category must correspond to behavior the patch code instruments. Client and server,
    or producer and consumer, must each be proven separately. Prefer the specific category over the underlying
    protocol: an LLM API call made over HTTP is ``gen_ai``, not ``http.client``. Use ``other``, on its own, only
    when nothing else fits.

``systems``
    A sorted list of the concrete backends or providers the integration is bound to, such as ``postgresql``,
    ``kafka``, ``redis``, ``aws.s3``, or ``openai``. A backend counts only when the integration's code is tied to
    it, for example through a dedicated client. Use ``[]`` when the backend is chosen at runtime, as with generic
    HTTP clients and web frameworks.

``creation_date``
    The quoted ``'YYYY-MM-DD'`` author date of the first commit that added working instrumentation. Commits that
    only add autopatching or registry entries, or that move files, do not count. For a new integration, this is
    the author date of the commit that adds the patch code. For an existing integration, start from:

    .. code-block:: bash

        git --no-pager log --follow --diff-filter=A --date=short --format='%H %ad %s' -- ddtrace/contrib/internal/<integration>/patch.py

    If the oldest result only moves or renames the file, repeat the command with the older path, for example
    ``ddtrace/contrib/<integration>/patch.py``, until you reach the commit that added working instrumentation.

``category`` and ``systems`` values come from a shared taxonomy that is not part of this repository. Only use values
that already appear in other manifests under ``ddtrace/contrib/internal/``, and never introduce a new one. If no
category fits, use ``other``; if no system fits, use ``[]``. If you think a new value is needed, or you are unsure
which value applies, say so in the pull request description so a reviewer can confirm it.

Most changes to an existing integration do not touch its manifest. Bug fixes, refactors, new tags,
configuration options, and supported-version changes leave it as is. Update the manifest only when:

- The packages that activate the integration change. Update ``packages`` together with ``registry.yaml``.
- The integration starts or stops instrumenting a kind of operation, for example a client-only integration that
  adds server spans. Add or remove the ``category``.
- The integration adds or drops dedicated support for a backend or provider. Add or remove the ``systems`` entry.
- ``description`` no longer matches what the integration instruments, for example because a behavior it names was
  removed. Update ``description``.
- The integration is renamed. Update ``name`` and ``display_name``.

Never change ``creation_date`` on an existing manifest. When an integration is removed, delete its manifest
along with the rest of its directory.

What does a complete PR look like when adding a new integration?
----------------------------------------------------------------

The following is the check list for ensuring you have all of the components to have a complete PR that is ready for review.

- Define `patch` and `unpatch` functions for your new integration under ``ddtrace/contrib/internal/your_integration_name``.
- Document your integration in a ``ddtrace/contrib/internal/<integration_name>/__init__.py`` module and reference the doc string in ``docs/integrations.rst``.
- An integration manifest at ``ddtrace/contrib/internal/<integration_name>/<integration_name>.manifest.yaml`` (see "What is an integration manifest?" above).
- Test code for the above in ``tests/contrib/your_integration_name``.
- The test environment and GitLab CI configuration in ``tests/contrib/suitespec.yml``.
- Your integration added to ``PATCH_MODULES`` in ``ddtrace/_monkey.py`` to enable auto instrumentation for it.
- The relevant file paths for your integration added to a suitespec file (see ``tests/README.md`` for details).
- A release note for your addition generated with ``uvx --from 'reno==4.1.0' reno new YOUR_TITLE_SLUG``, which will add ``releasenotes/notes/YOUR_TITLE_SLUG.yml``.
