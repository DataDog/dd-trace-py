Upgrading
---------

.. _upgrade-0.x:

Upgrade 0.x
^^^^^^^^^^^

.. _`Upgrading and deprecation warnings`:

Deprecation warnings
********************

As of v0.60.0, the library provides `Python
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


Environment variables
*********************

Use the following patterns to identify the deprecated environment variables in a code base::

    git grep -e "DATADOG_[A-Z_]*" \
      -e "DATADOG_SERVICE_NAME" \
      -e "DD_CALL_BASIC_CONFIG" \
      -e "DD_LOGGING_RATE_LIMIT" \
      -e "DD_SERVICE_NAME" \
      -e "DD_TRACER_PARTIAL_FLUSH_ENABLED" \
      -e "DD_TRACER_PARTIAL_FLUSH_MIN_SPANS"
