mod libddwaf;

use pyo3::{pymodule, types::PyModule, types::PyModuleMethods as _, Bound, PyResult};

#[pymodule]
pub mod appsec {
    #[pymodule_export]
    use super::libddwaf::libddwaf;
}

pub(crate) fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_wrapped(pyo3::wrap_pymodule!(appsec))?;
    Ok(())
}
