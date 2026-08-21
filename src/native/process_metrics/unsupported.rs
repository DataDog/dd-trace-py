//! Fallback for platforms without a native implementation (anything other than
//! Linux, macOS, or Windows). Keeps the crate compiling everywhere; callers see
//! this as a Python exception, matching `ValueCollector`'s existing
//! required_modules-failure -> disabled degrade path.

use pyo3::exceptions::PyNotImplementedError;
use pyo3::prelude::*;

pub fn process_metrics() -> PyResult<(u64, u64, i64, i64, u64, u64)> {
    Err(PyNotImplementedError::new_err(
        "process_metrics() is not implemented on this platform",
    ))
}

pub fn total_memory_bytes() -> PyResult<u64> {
    Err(PyNotImplementedError::new_err(
        "total_memory_bytes() is not implemented on this platform",
    ))
}
