use crate::context::ContextData;
use crate::span::SpanData;
use libdd_otel_thread_ctx::linux::ThreadContext;
use pyo3::{pyfunction, PyRef};

const UNKNOWN_LOCAL_ROOT_SPAN_ID: u64 = 0;

fn update_thread_context(trace_id: u128, span_id: u64, trace_flags: u8, local_root_span_id: u64) {
    ThreadContext::update(
        trace_id.to_be_bytes(),
        span_id.to_be_bytes(),
        trace_flags,
        local_root_span_id.to_be_bytes(),
        &[],
    );
}

#[pyfunction]
pub fn update_otel_thread_context(
    span: PyRef<'_, SpanData>,
    local_root: Option<PyRef<'_, SpanData>>,
    trace_flags: u8,
) {
    let local_root_span_id = if let Some(local_root) = local_root {
        local_root.span_id
    } else {
        span.span_id
    };

    update_thread_context(span.trace_id, span.span_id, trace_flags, local_root_span_id);
}

#[pyfunction]
pub fn update_otel_thread_context_from_context(context: PyRef<'_, ContextData>, trace_flags: u8) {
    let Some(trace_id) = context.trace_id.filter(|trace_id| *trace_id != 0) else {
        ThreadContext::detach();
        return;
    };
    let Some(span_id) = context
        .span_id
        .filter(|span_id| *span_id != 0)
        .and_then(|span_id| u64::try_from(span_id).ok())
    else {
        ThreadContext::detach();
        return;
    };

    update_thread_context(trace_id, span_id, trace_flags, UNKNOWN_LOCAL_ROOT_SPAN_ID);
}

#[pyfunction]
pub fn detach_otel_thread_context() {
    ThreadContext::detach();
}
