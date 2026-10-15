use pyo3::types::PyModuleMethods as _;
use std::sync::atomic::{AtomicBool, Ordering};

/// Whether errors in event listeners should be re-raised (DD_TESTING_RAISE).
///
/// Default is `false`. Set through the process-global Python config via
/// `ddtrace.internal.native.config.set_raise` at startup.
static RAISE: AtomicBool = AtomicBool::new(false);

#[inline(always)]
pub fn get_raise() -> bool {
    RAISE.load(Ordering::Relaxed)
}

#[inline(always)]
pub fn set_raise(val: bool) {
    RAISE.store(val, Ordering::Relaxed);
}

#[pyo3::pyfunction(name = "get_raise")]
fn get_raise_py() -> bool {
    get_raise()
}

#[pyo3::pyfunction(name = "set_raise")]
fn set_raise_py(val: bool) {
    set_raise(val);
}

#[pyo3::pymodule(name = "config")]
pub fn config_module(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    m.add_function(pyo3::wrap_pyfunction!(get_raise_py, m)?)?;
    m.add_function(pyo3::wrap_pyfunction!(set_raise_py, m)?)?;
    Ok(())
}
