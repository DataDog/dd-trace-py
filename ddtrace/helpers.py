import ddtrace


def get_correlation_ids():
    """Retrieves the Correlation Identifiers for the current active ``Trace``.
    This helper method can be achieved manually and should be considered
    only a shortcut. The main reason is to abstract the current ``Tracer``
    implementation so that these identifiers can be extracted either the
    tracer is an OpenTracing tracer or a Datadog tracer.

    OpenTracing users can still extract these values using the ``ScopeManager``
    API, though this shortcut is a simple one-liner. The usage is:

        from ddtrace import correlation

        trace_id, span_id = correlation.get_correlation_ids()

    :returns: a tuple containing the trace_id and span_id
    """
    # Consideration: currently we don't have another way to "define" a
    # GlobalTracer. In the case of OpenTracing, ``opentracing.tracer`` is exposed
    # and we're doing the same here for ``ddtrace.tracer``. Because this helper
    # must work also with OpenTracing, we should take the right used ``Tracer``.
    # At the time of writing, it's enough to support our Datadog Tracer.
    tracer = ddtrace.tracer
    span = tracer.current_span()
    if span is None:
        return None, None
    return span.trace_id, span.span_id
