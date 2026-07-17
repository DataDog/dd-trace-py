use crate::span::SpanData;
use libdd_otel_thread_ctx::linux::ThreadContext;
use pyo3::{pyfunction, PyRef, PyResult};

#[pyfunction]
pub fn update_otel_thread_context(
    span: PyRef<'_, SpanData>,
    local_root: Option<PyRef<'_, SpanData>>,
) {
    let local_root_span_id = if let Some(local_root) = local_root {
        local_root.span_id
    } else {
        span.span_id
    };

    ThreadContext::update(
        span.trace_id.to_be_bytes(),
        span.span_id.to_be_bytes(),
        local_root_span_id.to_be_bytes(),
        &[],
    );
}

#[pyfunction]
pub fn detach_otel_thread_context() {
    ThreadContext::detach();
}
