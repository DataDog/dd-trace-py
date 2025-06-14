mod crashtracker;
mod ddsketch;
mod library_config;

use pyo3::prelude::*;
use pyo3::wrap_pyfunction;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ddsketch::DDSketchPy>()?;
    m.add_class::<library_config::PyConfigurator>()?;

    #[cfg(all(unix, feature = "crashtracker"))]
    {
        m.add_class::<crashtracker::StacktraceCollectionPy>()?;
        m.add_class::<crashtracker::CrashtrackerConfigurationPy>()?;
        m.add_class::<crashtracker::CrashtrackerReceiverConfigPy>()?;
        m.add_class::<crashtracker::CrashtrackerMetadataPy>()?;
        m.add_class::<crashtracker::CrashtrackerStatus>()?;
        m.add_function(wrap_pyfunction!(crashtracker::crashtracker_init, m)?)?;
        m.add_function(wrap_pyfunction!(crashtracker::crashtracker_on_fork, m)?)?;
        m.add_function(wrap_pyfunction!(crashtracker::crashtracker_status, m)?)?;
        m.add_function(wrap_pyfunction!(crashtracker::crashtracker_receiver, m)?)?;
    }
    m.add_class::<library_config::PyTracerMetadata>()?;
    m.add_class::<library_config::PyAnonymousFileHandle>()?;
    m.add_wrapped(wrap_pyfunction!(library_config::store_metadata))?;
    Ok(())
}
