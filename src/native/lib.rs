#[allow(clippy::useless_conversion)]
mod crashtracker;
#[allow(clippy::useless_conversion)]
mod ddsketch;
#[allow(clippy::useless_conversion)]
mod library_config;

use pyo3::prelude::*;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ddsketch::DDSketchPy>()?;
    m.add_class::<library_config::PyConfigurator>()?;
    m.add_class::<crashtracker::CrashtrackerConfigurationPy>()?;
    m.add_class::<crashtracker::CrashtrackerReceiverConfigPy>()?;
    m.add_class::<crashtracker::MetadataPy>()?;
    m.add_function(wrap_pyfunction!(crashtracker::crashtracker_init, m)?)?;
    m.add_function(wrap_pyfunction!(crashtracker::crashtracker_on_fork, m)?)?;
    Ok(())
}
