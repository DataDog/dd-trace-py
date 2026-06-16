use pyo3::{
    exceptions::PyTypeError,
    prelude::*,
    types::{PyDict, PyMapping, PyTuple},
};

/// Python-facing `AgentResponse` object passed to the response callback after each
/// successful export that returns a changed sampling-rate payload.
#[pyclass(name = "AgentResponse")]
pub struct AgentResponsePy {
    #[pyo3(get)]
    pub(super) rate_by_service: Py<PyDict>,
}

#[pymethods]
impl AgentResponsePy {
    #[new]
    fn new(py: Python<'_>, rate_by_service: Bound<'_, PyAny>) -> PyResult<Self> {
        let dict = if let Ok(d) = rate_by_service.cast::<PyDict>() {
            d.copy()?
        } else if let Ok(m) = rate_by_service.cast::<PyMapping>() {
            let dict = PyDict::new(py);
            if let Ok(items) = m.items() {
                for item in items.iter() {
                    let Ok(pair) = item.cast::<PyTuple>() else {
                        continue;
                    };
                    let Ok(k) = pair.get_item(0) else {
                        continue;
                    };
                    let Ok(v) = pair.get_item(1) else {
                        continue;
                    };
                    dict.set_item(k, v)?;
                }
            }
            dict
        } else {
            return Err(PyTypeError::new_err(
                "rate_by_service must be a Mapping[str, float]",
            ));
        };
        Ok(Self {
            rate_by_service: dict.unbind(),
        })
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
