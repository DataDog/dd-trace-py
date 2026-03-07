"""
The MLflow integration instruments the ``mlflow`` package.

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
"""  # noqa: E501
