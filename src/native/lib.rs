mod data_pipeline;
mod ddsketch;
mod library_config;

use pyo3::prelude::*;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ddsketch::DDSketchPy>()?;
    m.add_class::<library_config::PyConfigurator>()?;
    data_pipeline::register_data_pipeline(m)?;

    Ok(())
}
