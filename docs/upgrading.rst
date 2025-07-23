Upgrading
---------

.. _`Upgrading and deprecation warnings`:

Deprecation warnings
********************

As of v0.60.0, the ddtrace library provides `Python
warnings <https://docs.python.org/3/library/warnings.html>`_ for deprecations
with an additional warning category ``ddtrace.DDTraceDeprecationWarning``.

This warning category can be used in configuring pytest `warnings capture <https://docs.pytest.org/en/6.2.x/warnings.html>`_ to turn warnings into errors::

    pytest -W "error::ddtrace.DDTraceDeprecationWarning" tests.py


In addition, the environment variable ``DD_TRACE_RAISE_DEPRECATIONWARNING`` is provided to configure the warning filter to raise an exception in an application instrumented with ddtrace::

    DD_TRACE_RAISE_DEPRECATIONWARNING=1 ddtrace-run python app.py


Before v0.60.0, you must enable all deprecation warnings and filter the application or tests logs for deprecations specific to the ddtrace library::

    $ python -Wall app.py

    # or

    $ PYTHONWARNINGS=all python app.py



.. _upgrade-0.x:

Upgrade to 1.0
**************

Environment variables
^^^^^^^^^^^^^^^^^^^^^

Use the following patterns to identify the deprecated environment variables in a code base::

    git grep -e "DATADOG_[A-Z_]*" \
      -e "DATADOG_SERVICE_NAME" \
      -e "DD_CALL_BASIC_CONFIG" \
      -e "DD_LOGGING_RATE_LIMIT" \
      -e "DD_SERVICE_NAME" \
      -e "DD_TRACER_PARTIAL_FLUSH_ENABLED" \
      -e "DD_TRACER_PARTIAL_FLUSH_MIN_SPANS"


.. _upgrade-1.x:

Upgrade to 2.0
**************

Environment variables
^^^^^^^^^^^^^^^^^^^^^

Use the following patterns to identify the deprecated environment variables in a code base::

    git grep -e "DD_GEVENT_PATCH_ALL" \
      -e "DD_AWS_TAG_ALL_PARAMS" \
      -e "DD_REMOTECONFIG_POLL_SECONDS" \
      -e "DD_CALL_BASIC_CONFIG"

Legacy tracing interfaces
^^^^^^^^^^^^^^^^^^^^^^^^^

Reference the 2.0 release note (``../releasenotes/notes/release-2.0-3af0045e2261bd02.yaml``) to identify and remove the deprecated legacy tracing
interfaces in a code base.

.. _upgrade-2.x:

Upgrade to 3.0
**************

Environment variables
^^^^^^^^^^^^^^^^^^^^^

Use the following patterns to identify the deprecated environment variables in a code base. These configurations will not be supported in v3.0. Refer to the migration guide below for next steps::

    git grep -P -e "DD_LLMOBS_APP_NAME" \
        -e "_DD_LLMOBS_EVALUATOR_SAMPLING_RULES" \
        -e "_DD_LLMOBS_EVALUATORS" \
        -e "DD_TRACE_PROPAGATION_STYLE=.*b3 single header" \
        -e "DD_TRACE_SAMPLE_RATE" \
        -e "DD_TRACE_API_VERSION=v0.3" \
        -e "DD_ANALYTICS_ENABLED" \
        -e "DD_TRACE_ANALYTICS_ENABLED" \
        -e "DD_HTTP_CLIENT_TAG_QUERY_STRING" \
        -e "DD_TRACE_SPAN_AGGREGATOR_RLOCK" \
        -e "DD_TRACE_METHODS=.*\[\]"

Legacy tracing interfaces
^^^^^^^^^^^^^^^^^^^^^^^^^

Reference the [3.0 release note](https://github.com/DataDog/dd-trace-py/releases/tag/v3.0.0) to identify and update deprecated interfaces in ddtrace products.