use pyo3::{
    types::{PyAnyMethods as _, PyInt, PyList, PyModule, PyModuleMethods as _},
    Bound, PyAny, PyResult, Python,
};

use crate::py_string::PyBackedString;

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
        _name: &Bound<'_, PyAny>,
        _attributes: Option<&Bound<'_, PyAny>>,
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
        tracestate: Option<&Bound<'p, PyAny>>,
        flags: Option<&Bound<'p, PyInt>>,
        attributes: Option<&Bound<'p, PyAny>>,
        _dropped_attributes: u32,
    ) -> PyResult<()> {
        Ok(())
    }
}

#[pyo3::pyclass(name = "SpanData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct SpanData {
    data: libdd_trace_utils::span::Span<PyBackedString>,
}

/// Extract PyBackedString from Python object, falling back to empty string on error
#[inline]
fn extract_backed_string_or_default(obj: &Bound<'_, PyAny>) -> PyBackedString {
    obj.extract::<PyBackedString>().unwrap_or_default()
}

/// Extract PyBackedString from Python object, falling back to None on error
#[inline]
fn extract_backed_string_or_none(obj: &Bound<'_, PyAny>) -> PyBackedString {
    let py = obj.py();
    obj.extract::<PyBackedString>()
        .unwrap_or_else(|_| PyBackedString::py_none(py))
}

#[pyo3::pymethods]
impl SpanData {
    #[new]
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
    fn __new__<'p>(
        py: Python<'p>,
        name: &Bound<'p, PyAny>,
        service: Option<&Bound<'p, PyAny>>,
        resource: Option<&Bound<'p, PyAny>>,
        span_type: Option<&Bound<'p, PyAny>>,
        trace_id: Option<&Bound<'p, PyInt>>,
        span_id: Option<&Bound<'p, PyInt>>,
        parent_id: Option<&Bound<'p, PyInt>>,
        start: Option<f64>,
        span_api: Option<&Bound<'p, PyAny>>,
        links: Option<&Bound<'p, PyList>>,
    ) -> Self {
        let mut span = Self::default();
        // Initialize fields in __new__ since PyO3 doesn't automatically call __init__
        span.data.name = extract_backed_string_or_default(name);
        span.data.service = match service {
            Some(obj) => extract_backed_string_or_none(obj),
            None => PyBackedString::py_none(py),
        };
        span
    }

    #[getter]
    fn get_name<'py>(&self, py: Python<'py>) -> pyo3::Bound<'py, PyAny> {
        self.data.name.as_py(py)
    }

    #[setter]
    fn set_name(&mut self, name: &Bound<'_, PyAny>) {
        self.data.name = extract_backed_string_or_default(name);
    }

    #[getter]
    fn get_service<'py>(&self, py: Python<'py>) -> Option<pyo3::Bound<'py, PyAny>> {
        if self.data.service.is_py_none(py) {
            None
        } else {
            Some(self.data.service.as_py(py))
        }
    }

    #[setter]
    fn set_service(&mut self, service: &Bound<'_, PyAny>) {
        self.data.service = extract_backed_string_or_none(service);
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanLinkData>()?;
    m.add_class::<SpanEventData>()?;
    m.add_class::<SpanData>()?;
    Ok(())
}
