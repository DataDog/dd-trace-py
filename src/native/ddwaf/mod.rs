//! Native libddwaf bindings, exposed as the `ddwaf` submodule of `ddtrace.internal.native._native`.
//!
//! This is a one-for-one mirror of the C ABI that the previous ctypes layer in
//! `ddtrace/appsec/_ddwaf/` declared. All orchestration/build/read logic remains on the Python
//! side; this module only provides the typed wrappers and thin function bindings. libddwaf is
//! statically linked into the extension (no external `.so` is downloaded by setup.py or loaded at
//! runtime).

pub mod object;
pub mod waf;

use pyo3::prelude::*;

#[pymodule]
pub fn ddwaf(m: &Bound<'_, PyModule>) -> PyResult<()> {
    object::register(m)?;
    waf::register(m)?;
    Ok(())
}
