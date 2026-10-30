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

   The constructor option applies only to that provider instance. Providers that
   omit it, including the provider created by the LLM Observability prompt manager,
   use the environment setting. Set the environment variable before those providers
   are constructed to enable support for them as well.

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

   On OpenFeature 0.10 and later, ``api.set_provider`` does not wait for
   initialization. Evaluations while the provider is not ready return the default
   value. ``api.set_provider_and_wait`` waits, but raises ``ProviderNotReadyError``
   if no configuration arrives within the initialization timeout.

   Only boolean ``True`` suppresses new evaluation events, evaluation metrics,
   and automatic flag span annotations. The provider captures the final merged context
   at resolver entry, including contexts returned by ``before`` hooks. A returned
   ``False`` can override an earlier ``True`` according to the OpenFeature SDK's
   context precedence. Returned contexts apply only when evaluation reaches the
   resolver. The SDK skips ``before`` hooks while the provider is ``NOT_READY``
   or ``FATAL`` and discards earlier returns if a later ``before`` hook raises.
   Set the subject attribute in the evaluation context before evaluation so that
   opt-out also applies in those cases. Do not rely on an ``after`` hook to change
   opt-out behavior across SDKs. Existing queued telemetry remains intact.

   ``dd_evaluation_telemetry_opt_out`` remains an ordinary context attribute.
   Targeting rules can match on it, so changing it can change an assignment and
   produce a new exposure. Experiment exposures are still reported for opted-out
   subjects. Each exposure includes the targeting key and all context attributes,
   including ``dd_evaluation_telemetry_opt_out``. When the attribute is not ``True``,
   evaluation events include it only when full evaluation data is enabled for the
   environment. Opt-out suppresses evaluation telemetry in both reporting modes.
   Local debug logging is controlled separately and can include raw evaluation data.

   Telemetry suppression does not disable experiment exposures or change flag
   values, variants, randomization, or application hooks.
