use pyo3::{prelude::*, types::PyDict};

/// Python-facing `AgentResponse` object passed to the response callback after each
/// successful export that returns a changed sampling-rate payload.
#[pyclass(name = "AgentResponse")]
pub struct AgentResponsePy {
    #[pyo3(get)]
    rate_by_service: Py<PyDict>,
}

#[pymethods]
impl AgentResponsePy {
    #[new]
    fn new(rate_by_service: Py<PyDict>) -> Self {
        Self { rate_by_service }
    }

    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        visit.call(&self.rate_by_service)?;
        Ok(())
    }

    fn __clear__(&mut self) {}
}

pub fn register_agent_response(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<AgentResponsePy>()?;
    Ok(())
}
