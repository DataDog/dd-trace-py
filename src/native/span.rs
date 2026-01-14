use pyo3::{Bound, PyObject, PyResult, Python, types::{PyList, PyModule, PyModuleMethods as _}};

#[pyo3::pyclass(name = "SpanData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct SpanData {}

#[pyo3::pymethods]
impl SpanData {
    #[new]
    #[pyo3(signature =(
        *_py_args,
        **_py_kwargs,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn __new__(
        _py_args: &pyo3::Bound<'_, pyo3::types::PyTuple>,
        _py_kwargs: Option<&pyo3::Bound<'_, pyo3::types::PyDict>>,
    ) -> Self {
        Self::default()
    }

    /// Performs span initialization
    ///
    /// This can not be put on new, because otherwise the signature needs to match
    /// for every inherited class
    fn __init__<'py>(
        &mut self,
        _py: Python<'py>,
        _name: PyObject,
        _service: Option<PyObject>,
        _resource: Option<PyObject>,
        _span_type: Option<PyObject>,
        _trace_id: Option<u128>,
        _span_id: Option<u64>,
        _parent_id: Option<u64>,
        _start: Option<f64>,
        _span_api: PyObject,
        _links: Option<Bound<'py, PyList>>,
    ) -> PyResult<()> {
        Ok(())
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanData>()?;
    Ok(())
}
