"""
The MLflow integration instruments MLflow run lifecycle and logging concepts.

It traces run start/end boundaries and step progression, captures parameter and
metric logging during active runs.

Enabling
~~~~~~~~

The MLflow integration is not enabled by default. Enable it with
:ref:`ddtrace-run<ddtracerun>` using ``DD_TRACE_MLFLOW_ENABLED=true`` or
``DD_PATCH_MODULES=mlflow:true``, or call :func:`patch() <ddtrace.patch>`.
When ``DD_MODEL_LAB_ENABLED=true``, the MLflow integration is enabled
automatically.

Manual patching example::

    from ddtrace import patch
    patch(mlflow=True)

Log injection
~~~~~~~~~~~~~

MLflow log injection is disabled by default. Enable it with
``DD_TRACE_MLFLOW_LOGS_INJECTION=true``; it is also enabled automatically when
``DD_MODEL_LAB=true``.

When enabled, your logging format must include ``%(dd.mlflow.run_id)s`` to
safely reference the injected run ID field. For example, this format string
includes run correlation with trace correlation fields:

.. code-block:: python

    FORMAT = (
        "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] "
        "[dd.mlflow.run_id=%(dd.mlflow.run_id)s dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] "
        "- %(message)s"
    )

Outside an active ``mlflow.start_run()`` context, ``dd.mlflow.run_id`` is
empty. Inside a run, it is populated with the active MLflow run ID.

MLflow auth plugin
~~~~~~~~~~~~~~~~~~

If environment variables ``DD_API_KEY``, ``DD_APP_KEY``, and
``DD_MODEL_LAB_ENABLED`` are set, the MLflow authentication plugin
includes ``DD-API-KEY`` and ``DD-APPLICATION-KEY`` headers in requests
to the MLflow tracking server.
"""
