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
    pub fn __new__(
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
    pub fn __new__(
        _py_args: &pyo3::Bound<'_, pyo3::types::PyTuple>,
        _py_kwargs: Option<&pyo3::Bound<'_, pyo3::types::PyDict>>,
    ) -> Self {
        Self::default()
    }

    #[pyo3(signature = (
        trace_id,
        span_id,
        tracestate = None,
        flags = None,
        attributes = None,
        _dropped_attributes = 0,
    ))]
    #[allow(unused_variables)]
    pub fn __init__<'p>(
        &mut self,
        trace_id: &Bound<'p, PyInt>,
        span_id: &Bound<'p, PyInt>,
        tracestate: Option<PyObject>,
        flags: Option<&Bound<'p, PyInt>>,
        attributes: Option<PyObject>,
        _dropped_attributes: u32,
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
    #[allow(clippy::too_many_arguments)]
    #[allow(unused_variables)]
    #[pyo3(signature = (
        name,
        service = None,
        resource = None,
        span_type = None,
        trace_id = None,
        span_id = None,
        parent_id = None,
        start = None,
        span_api = None,
        links = None,
    ))]
    fn __init__<'p>(
        &mut self,
        _py: Python<'p>,
        name: PyObject,
        service: Option<PyObject>,
        resource: Option<PyObject>,
        span_type: Option<PyObject>,
        trace_id: Option<&Bound<'p, PyInt>>,
        span_id: Option<&Bound<'p, PyInt>>,
        parent_id: Option<&Bound<'p, PyInt>>,
        start: Option<f64>,
        span_api: Option<PyObject>,
        links: Option<Bound<'p, PyList>>,
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
