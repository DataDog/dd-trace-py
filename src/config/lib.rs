mod library_config;

use pyo3::prelude::*;

#[pymodule]
fn _config(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<library_config::PyConfigurator>()?;
    Ok(())
}
