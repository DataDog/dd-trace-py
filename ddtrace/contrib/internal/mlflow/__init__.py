"""
The MLflow integration instruments MLflow run lifecycle and logging concepts.

It traces run start/end boundaries and step progression, captures parameter and
metric logging during active runs.

Enabling
~~~~~~~~

The MLflow integration is enabled automatically when you use
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.
Alternatively, use :func:`patch() <ddtrace.patch>` to manually enable the MLflow integration::

    from ddtrace import patch
    patch(mlflow=True)

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.mlflow["service"]
   The service name reported by default for MLflow requests.
   Alternatively, set this option with the ``DD_MLFLOW_SERVICE`` environment variable.

Log injection
~~~~~~~~~~~~~

When log injection is enabled, logs emitted during an active MLflow run
includes the current run ID via ``%(dd.mlflow.run_id)s``. For example, this
format string includes run correlation with trace correlation fields:

.. code-block:: python

    FORMAT = (
        "%(asctime)s %(levelname)s [%(name)s] [%(filename)s:%(lineno)d] "
        "[dd.mlflow.run_id=%(dd.mlflow.run_id)s dd.trace_id=%(dd.trace_id)s dd.span_id=%(dd.span_id)s] "
        "- %(message)s"
    )

Outside an active ``mlflow.start_run()`` context, ``dd.mlflow.run_id`` is
empty. Inside a run, it is populated with the active MLflow run ID.
"""  # noqa: E501
