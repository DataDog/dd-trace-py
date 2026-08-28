use pyo3::types::PyModuleMethods as _;

mod context_data;

pub use context_data::Context;

pub fn register_context(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    m.add_class::<Context>()?;
    Ok(())
}
