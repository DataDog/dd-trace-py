use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use datadog_ddsketch::DDSketch;

#[pyclass(name = "DDSketch", module = "ddtrace.internal._core")]
pub struct DDSketchPy {
    ddsketch: DDSketch,
}

#[pymethods]
impl DDSketchPy {
    #[new]
    fn new() -> Self {
        DDSketchPy {
            ddsketch: DDSketch::default(),
        }
    }

    #[getter]
    fn count(&self) -> f64 {
        self.ddsketch.count()
    }

    fn add(&mut self, value: f64) -> PyResult<()> {
        match self.ddsketch.add(value) {
            Ok(_) => Ok(()),
            Err(e) => Err(PyValueError::new_err(e.to_string())),
        }
    }

    fn to_proto<'p>(&self, py: Python<'p>) -> Bound<'p, PyBytes> {
        let res = self.ddsketch.clone().encode_to_vec();
        PyBytes::new_bound(py, &res)
    }
}
