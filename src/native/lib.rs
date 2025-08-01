#[cfg(feature = "profiling")]
pub use datadog_profiling_ffi::*;
mod ddsketch;
mod library_config;

use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

/// Dummy function to check if imported lib is generated on windows builds.
#[no_mangle]
pub extern "C" fn ddtrace_force_export_for_windows() {}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ddsketch::DDSketchPy>()?;
    m.add_class::<library_config::PyConfigurator>()?;
    m.add_class::<library_config::PyTracerMetadata>()?;
    m.add_class::<library_config::PyAnonymousFileHandle>()?;
    m.add_wrapped(wrap_pyfunction!(library_config::store_metadata))?;
    Ok(())
}
