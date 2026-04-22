use pyo3::types::PyModuleMethods as _;
use std::sync::atomic::{AtomicBool, Ordering};

/// Whether 128-bit trace IDs should be generated for new spans.
///
/// Default is `true`. Initialized by Python `Config.__init__` via
/// `ddtrace.internal.native.config.set_128_bit_trace_id_enabled` shortly after process startup.
/// The default covers the window before Python config is loaded.
///
/// Unlike the old `LazyBool` design, this value is writable at any time, so
/// `override_global_config(dict(_128_bit_trace_id_enabled=...))` works correctly in tests
/// without requiring subprocess isolation.
static ENABLED: AtomicBool = AtomicBool::new(true);

#[inline(always)]
pub fn get_128_bit_trace_id_enabled() -> bool {
    ENABLED.load(Ordering::Relaxed)
}

#[inline(always)]
pub fn set_128_bit_trace_id_enabled(val: bool) {
    ENABLED.store(val, Ordering::Relaxed);
}

#[pyo3::pyfunction(name = "get_128_bit_trace_id_enabled")]
fn get_128_bit_trace_id_enabled_py() -> bool {
    get_128_bit_trace_id_enabled()
}

#[pyo3::pyfunction(name = "set_128_bit_trace_id_enabled")]
fn set_128_bit_trace_id_enabled_py(val: bool) {
    set_128_bit_trace_id_enabled(val);
}

#[pyo3::pymodule(name = "config")]
pub fn config_module(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    m.add_function(pyo3::wrap_pyfunction!(get_128_bit_trace_id_enabled_py, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(set_128_bit_trace_id_enabled_py, m)?)?;
    Ok(())
}
