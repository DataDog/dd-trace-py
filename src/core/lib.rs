use pyo3::prelude::*;

#[pymodule]
fn _core(_: &Bound<'_, PyModule>) -> PyResult<()> {
    Ok(())
}
