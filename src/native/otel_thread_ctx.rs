//! Publishes the active span's trace/span ids into the thread-local record read by
//! out-of-process readers, per the Thread Context OTEP 4947.
//!
//! Linux-only: `libdd-otel-thread-ctx` is only compiled in for that target (see
//! `Cargo.toml`'s `[target.'cfg(target_os = "linux")'.dependencies]`).

use libdd_otel_thread_ctx::linux::ThreadContext;
use pyo3::prelude::*;

#[pyfunction]
pub fn on_context_activate(
    trace_id: Option<u128>,
    span_id: Option<u64>,
    local_root_span_id: Option<u64>,
) {
    if let (Some(trace_id), Some(span_id), Some(local_root_span_id)) =
        (trace_id, span_id, local_root_span_id)
    {
        ThreadContext::update(
            trace_id.to_be_bytes(),
            span_id.to_be_bytes(),
            local_root_span_id.to_be_bytes(),
            &[],
        );
        return;
    }
    ThreadContext::detach();
}

/// Verifies that this extension was linked so that `otel_thread_ctx_v1` is discoverable by an
/// out-of-process reader (exported as TLS GLOBAL in the dynamic symbol table, TLSDESC-only
/// relocations). Raises if not, e.g. a build regression that silently breaks the OTel thread
/// context feature. See `tests/tracer/test_context.py`.
#[pyfunction]
pub fn otel_thread_ctx_sanity_check() -> PyResult<()> {
    libdd_otel_thread_ctx::sanity_check::sanity_check()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
}

pub fn register_otel_thread_ctx(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(on_context_activate, m)?)?;
    m.add_function(wrap_pyfunction!(otel_thread_ctx_sanity_check, m)?)?;
    Ok(())
}
