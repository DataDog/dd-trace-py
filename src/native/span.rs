use pyo3::{
    types::{PyInt, PyList, PyModule, PyModuleMethods as _},
    Bound, PyObject, PyResult, Python,
};

#[pyo3::pyclass(name = "SpanEventData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct SpanEventData {}

#[pyo3::pymethods]
impl SpanEventData {
    #[new]
    #[pyo3(signature =(
        *_py_args,
        **_py_kwargs,
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn __new__<'py>(
        _py_args: &pyo3::Bound<'_, pyo3::types::PyTuple>,
        _py_kwargs: Option<&pyo3::Bound<'_, pyo3::types::PyDict>>,
    ) -> Self {
        Self::default()
    }

    pub fn __init__(
        &mut self,
        _name: PyObject,
        _attributes: Option<PyObject>,
        _time_unix_nano: Option<u64>,
    ) -> PyResult<()> {
        Ok(())
    }
}

#[pyo3::pyclass(name = "SpanLinkData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct SpanLinkData {}

#[pyo3::pymethods]
impl SpanLinkData {
    #[new]
    #[pyo3(signature =(
        *_py_args,
        **_py_kwargs,
    ))]
    #[allow(clippy::too_many_arguments)]
    pub fn __new__(
        _py_args: &pyo3::Bound<'_, pyo3::types::PyTuple>,
        _py_kwargs: Option<&pyo3::Bound<'_, pyo3::types::PyDict>>,
    ) -> Self {
        Self::default()
    }

    pub fn __init__<'p>(
        &mut self,
        _trace_id: &Bound<'p, PyInt>,
        _span_id: &Bound<'p, PyInt>,
        _tracestate: PyObject,
        _flags: Option<&Bound<'p, PyInt>>,
        _attributes: Option<PyObject>,
        _dropped_attributes: &Bound<'p, PyInt>,
    ) -> PyResult<()> {
        Ok(())
    }
}

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
    fn __init__<'p>(
        &mut self,
        _py: Python<'p>,
        _name: PyObject,
        _service: Option<PyObject>,
        _resource: Option<PyObject>,
        _span_type: Option<PyObject>,
        _trace_id: Option<&Bound<'p, PyInt>>,
        _span_id: Option<&Bound<'p, PyInt>>,
        _parent_id: Option<&Bound<'p, PyInt>>,
        _start: Option<f64>,
        _span_api: PyObject,
        _links: Option<Bound<'p, PyList>>,
    ) -> PyResult<()> {
        Ok(())
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanLinkData>()?;
    m.add_class::<SpanEventData>()?;
    m.add_class::<SpanData>()?;
    Ok(())
}
