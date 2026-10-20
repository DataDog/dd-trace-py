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

HTTP request-data contract
~~~~~~~~~~~~~~~~~~~~~~~~~~

This experimental internal API exposes HTTP observations without requiring tracing
configuration. Register for phase-specific events such as ``HttpRouteResolvedEvent``
and ``HttpRequestCompletedEvent`` from ``ddtrace.contrib._events.http`` using
``core.on(EventClass.event_name, listener)``. Read ``event.request``, ``event.response``,
``event.phase``, and ``event.request_id`` directly; there is no snapshot wrapper.
The shared ``HttpRequestDataEvent`` base supplies fields, not a subscription name.
Events carry no span, resource, service, operation name, or integration configuration.
The shared ``HttpServerRequestEvent`` opens the tracing context. Its subscriber
correlates HTTP observations by ``request_id`` and selects framework conventions
using the ``framework`` field. Frameworks without a conventions override receive
a request span using generic HTTP defaults; no tracing allowlist entry is required.
The correlation registry holds contexts weakly: the request lifecycle owns their
lifetime. Completion removes entries explicitly; garbage collection can remove
abandoned entries without keeping request state alive.
See ``examples/http-events/contract.py`` for consumer registration and blocking.

Shared producer lifecycle
^^^^^^^^^^^^^^^^^^^^^^^^^

``ddtrace.contrib.internal.http_server.HttpLifecycle`` owns request identity,
HTTP observations, phase publication, block checks, accepted block preservation,
and idempotent completion. Construct it with ``HttpLifecycleConfig`` and owned
``HttpRequestData``. It uses ``create_http_server_context`` to open the tracing
context and publishes completion before exiting it. Consumers continue to receive
push-only events; lifecycle methods are for instrumentation producers.

For WSGI integrations, ``ddtrace.contrib.internal.http_wsgi.WsgiLifecycleDriver``
creates the controller and manages context activation, ``start_response``, body
replacement, HEAD suppression, iteration, errors, and close. Pass framework hooks
and a ``get_config`` callback, then wrap the application's WSGI callable with
``driver.wrap``. Returning ``None`` from ``get_config`` disables the wrapper for
that request, supporting plugin uninstall/reinstall without replacing hooks.

Framework hooks extract request data and convert validated blocking data into a
response exposing ``status``, ``status_code``, ``headerlist``, and byte ``body``.
Conversion preserves framework defaults and supplies Content-Length when allowed;
the driver rejects caller-supplied framing and hop-by-hop headers. Framework
routing/handler hooks use ``driver.current(environ)`` to find their controller,
call ``route_resolved`` and ``before_handler``, and enforce returned blocks using
the framework's response mechanism. Handled errors are recorded before the
framework converts them to ordinary response values. These hooks must survive
plugin skip settings and callback-cache resets.

Bottle implements these hooks in ``ddtrace.contrib.internal.bottle.lifecycle``;
its public ``TracePlugin`` retains configuration and plugin setup. Route-local
plugins retain their callback-only fallback because they cannot observe the WSGI
boundary. See ``examples/http-events/lifecycle.py`` for wiring. ASGI requires a
separate async transport driver; this migration supplies only the WSGI driver.

The application-installed Bottle plugin publishes this contract at these boundaries:

* ``REQUEST_STARTED``: request entry, before Bottle's request hooks and routing.
* ``ROUTE_RESOLVED``: successful routing, with the mounted route template available.
* ``BEFORE_HANDLER``: immediately before the resolved route callback is invoked.
  ``handler_started`` is still false at this boundary and becomes true when that
  callback begins. The callback includes the complete plugin chain. This boundary
  remains active when a route skips the trace plugin or all plugins.
* ``RESPONSE_READY``: Bottle has prepared status and headers, before calling the
  server's ``start_response``.
* ``RESPONSE_COMMITTED``: ``start_response`` has returned successfully. This is
  the integration's conservative commitment boundary, not proof of bytes sent on
  the network.
* ``COMPLETED``: response exhaustion, failure, or explicit close, exactly once.

These are observations, not a required sequence for every request. Routing failures
have no resolved-route or handler phase. Framework retries can repeat routing or
response preparation. A failed ``start_response`` does not produce a committed
phase. Streaming responses produce completion only when iteration finishes or the
server closes them.

Availability and lifetime
^^^^^^^^^^^^^^^^^^^^^^^^

``None`` means a field is unavailable, whereas an empty string or tuple is a known
empty value. In particular, ``response=None`` means no response has been prepared;
``request.route=None`` means no route template is available. Empty query strings
and empty parsed query pairs are available values. Bottle exposes the query pairs
as parsed by the framework, preserving repeated keys. The raw query string is also
available when exact wire ordering or encoding matters.

Events are ordinary dataclasses and consumers must treat them as read-only.
Their request, response, and error values are frozen dataclasses. Header and
query values are tuples of pairs; response headers retain duplicates, including
multiple ``Set-Cookie`` values. Header names must be compared case-insensitively.
Consumers may retain events after dispatch; each phase creates a fresh envelope,
so later lifecycle updates do not change earlier observations. Mutation is not a
decision channel and can affect other listeners receiving the same event.

``request_id`` is an opaque token with identity shared by all events of one
request in this process. It is independent of span IDs and distinct for nested or
interleaved requests. It is not a serializable or cross-process correlation ID.

``errors`` contains the most recently observed failure, or an empty tuple when no
failure has been observed. Errors contain a qualified type name and a message, with
``message=None`` if the exception cannot be converted to text. They retain neither
exception objects nor tracebacks. A failure after response commitment does not
rewrite the prepared response status.

Request and response bodies remain unavailable in Bottle. Producing these events
must not consume request bodies, buffer responses, or advance response streams.

Blocking and consumers
^^^^^^^^^^^^^^^^^^^^^^

Observation events are one-way: listener return values are ignored. A separate
``HttpRequestBlockCheckEvent`` dispatch follows observation at request entry,
route resolution, before the wrapped handler, and response preparation. Its fields
are the same HTTP observations. A consumer can raise ``HttpRequestBlock`` with
``status_code``, ``headers``, and a bytes ``body`` to request a replacement response.
Returning normally allows the next listener to run.

The first block interrupts the check, skipping remaining block-check listeners.
An invalid blocking response is ignored, but skipped listeners are not resumed.
Bottle validates status, headers, and body and owns transport framing. After a valid
block it skips subsequent block checks, including checks of its replacement response.
The accepted status, headers, and body are retained through application hooks and
reapplied before the first ``start_response`` attempt. If delivery fails and Bottle
retries with an error response, that attempt does not reuse the blocking body.
Ordinary listener exceptions retain core's normal isolation behavior; the testing
raise setting still propagates them. ``HttpRequestBlock`` inherits ``BaseException``
to use the hub's existing control-flow propagation. Raise it only from a block-check
listener, never an observation listener. Process-control exceptions still propagate.

Block checks are synchronous and run only before response commitment. Retaining an
event or raising from a different thread cannot control the request. There is no
acceptance callback, capability set, or post-commit stream termination API. Blocking
is not an application error; response and completion observations still report the
outcome, including delivery failures. See :doc:`http-contract-api-changes` for history.

Bottle emits events when a consumer is registered even if tracing is disabled,
without creating a request span. Consumers should register before request entry to
observe the complete lifecycle. With no tracing, observation, or block-check listeners,
the WSGI wrapper delegates directly to Bottle. Route-local plugins publish only route
resolution and callback completion; they have no WSGI boundary and emit no block checks.


Testing HTTP contract adapters
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Integrations adopting the HTTP server request-data contract should inherit
``HttpContractTests`` from ``tests.contrib.http_contract`` in a pytest class
whose name starts with ``Test``. Import ``observe_http_requests`` and
``register_http_block_checker`` into that test module to expose the shared
``observations`` and ``block_checker`` fixtures, and provide an
``http_adapter`` fixture implementing ``HttpContractAdapter``. See
``tests/contrib/bottle/test_http_contract.py`` for the first implementation.

The adapter registers a parameterized route, constructs a native response, and
sends a request through the real instrumented application. It returns the consumed
response bytes even for error statuses and closes the response. It must not emit
synthetic events or provide expected phase sequences. The shared suite owns the
assertions for lifecycle ordering, field availability, immutable values, blocking,
and operation without tracing. Framework route syntax is supplied through
``route_pattern``. Async integrations can drive their test client behind the
synchronous adapter interface; transport-level async behavior needs separate tests.

Keep framework tracing conventions and transport-specific cases, including WSGI
``start_response`` failures and streaming cleanup, in the integration's own tests.
When adopting the shared suite, add ``tests/contrib/http_contract.py`` to that
integration's suite paths so changes to the contract tests select its CI jobs.


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
