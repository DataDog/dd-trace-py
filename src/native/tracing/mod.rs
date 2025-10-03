use crate::define_event;
#[allow(unused_imports)] // Used in macro-generated code
use pyo3::{Py, PyAny};

/*
# Tracing Events

Events related to distributed tracing and span lifecycle.
*/
define_event!(
    tracer_span_started,
    fn(span: Py<PyAny>),
    "\"ddtrace._trace.span.Span\""
);

define_event!(
    tracer_span_finished,
    fn(span: Py<PyAny>),
    "\"ddtrace._trace.span.Span\""
);

define_event!(
    tracer_span_sampled,
    fn(span: Py<PyAny>),
    "\"ddtrace._trace.span.Span\""
);
