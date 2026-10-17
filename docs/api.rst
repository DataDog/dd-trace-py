***
API
***

Tracing
=======

.. py:module:: ddtrace

.. automodule:: ddtrace.auto

.. autofunction:: ddtrace.patch

.. autoclass:: ddtrace.trace.Tracer
    :members:

.. autoclass:: ddtrace.trace.Span
    :members:

    .. autoattribute:: context

.. autoclass:: ddtrace.trace.Context
    :members:
    :undoc-members:
    :inherited-members:

.. autoclass:: ddtrace.propagation.http.HTTPPropagator
    :members:

.. automodule:: ddtrace.opentelemetry
    :members:


Runtime Metrics
===============

.. autoclass:: ddtrace.runtime.RuntimeMetrics
    :members:


Dynamic Instrumentation
=======================

.. automodule:: ddtrace.debugging
    :members:


Source Code Integration
=======================

.. automodule:: ddtrace.sourcecode
    :members:


OpenFeature
===========

.. py:class:: ddtrace.openfeature.DataDogProvider(*args, initialization_timeout=None, enable_evaluation_telemetry_opt_out_parameter=None, **kwargs)

Per-evaluation telemetry opt-out is disabled by default. Enable it with
``DD_FEATURE_FLAGS_EVALUATION_TELEMETRY_PER_SUBJECT_OPT_OUT_ENABLED=true`` or the
constructor option below. An explicit constructor ``True`` or ``False`` overrides
the environment. An omitted option or ``None`` uses the environment setting.
The provider captures this setting at construction, not during evaluation.

The application supplies the consent decision in the evaluation context::

    from openfeature import api
    from openfeature.evaluation_context import EvaluationContext
    from ddtrace.openfeature import DataDogProvider

    provider = DataDogProvider(enable_evaluation_telemetry_opt_out_parameter=True)
    api.set_provider(provider)
    context = EvaluationContext(
        targeting_key="subject-id",
        attributes={"dd_evaluation_telemetry_opt_out": True},
    )
    value = api.get_client().get_boolean_value("flag-key", False, context)

Only boolean ``True`` suppresses new evaluation events, evaluation metrics,
and automatic flag span annotations. The provider captures the final merged context
at resolver entry, including contexts returned by ``before`` hooks. A returned
``False`` can override an earlier ``True`` according to the OpenFeature SDK's
context precedence. Later changes to the shared hook context can suppress pending
telemetry, but cannot restore it for an opted-out evaluation. Existing queued
telemetry remains intact.

This option does not disable experiment exposures. It does not change flag
values, variants, randomization, exposure caching, or application hooks.
