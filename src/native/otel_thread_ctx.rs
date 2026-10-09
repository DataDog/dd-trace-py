use crate::context::Context;
use crate::span::SpanData;
use libdd_otel_thread_ctx::linux::ThreadContext;
use pyo3::prelude::*;

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

fn trace_flags(context: &Bound<'_, Context>) -> PyResult<u8> {
    let priority = if context.is_exact_instance_of::<Context>() {
        context
            .try_borrow_mut()?
            .get_sampling_priority(context.py())?
    } else {
        Some(context.getattr("sampling_priority")?)
    };
    match priority {
        Some(priority) if !priority.is_none() => Ok(priority.gt(0)? as u8),
        _ => Ok(0),
    }
}

fn update_from_span(span: &Bound<'_, SpanData>) -> PyResult<()> {
    let local_root = span
        .try_borrow()?
        ._local_root
        .as_ref()
        .map(|root| root.bind(span.py()).clone())
        .unwrap_or_else(|| span.clone());
    let context = SpanData::get_context(&local_root)?;
    let trace_flags = trace_flags(&context)?;
    let local_root_span_id = local_root.try_borrow()?.span_id;
    let span = span.try_borrow()?;
    update_thread_context(span.trace_id, span.span_id, trace_flags, local_root_span_id);
    Ok(())
}

fn update_from_context(context: &Bound<'_, Context>) -> PyResult<()> {
    let trace_flags = trace_flags(context)?;
    let context = context.try_borrow()?;
    let Some(trace_id) = context.trace_id.filter(|trace_id| *trace_id != 0) else {
        ThreadContext::detach();
        return Ok(());
    };
    let Some(span_id) = context
        .span_id
        .filter(|span_id| *span_id != 0)
        .and_then(|span_id| u64::try_from(span_id).ok())
    else {
        ThreadContext::detach();
        return Ok(());
    };

    update_thread_context(trace_id, span_id, trace_flags, UNKNOWN_LOCAL_ROOT_SPAN_ID);
    Ok(())
}

#[pyfunction]
pub fn sync_otel_thread_context(ctx: &Bound<'_, PyAny>) -> PyResult<()> {
    let result = if let Ok(span) = ctx.cast::<SpanData>() {
        update_from_span(span)
    } else if let Ok(context) = ctx.cast::<Context>() {
        update_from_context(context)
    } else {
        ThreadContext::detach();
        Ok(())
    };
    // Listener errors can be suppressed by the event hub, so clear stale TLS first.
    if result.is_err() {
        ThreadContext::detach();
    }
    result
}
