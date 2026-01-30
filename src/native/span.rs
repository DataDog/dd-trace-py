use pyo3::{
    types::{PyAnyMethods as _, PyDict, PyInt, PyModule, PyModuleMethods as _, PyTuple},
    Bound, FromPyObject, PyAny, PyResult, Python,
};

use crate::{
    py_string::PyBackedString,
    rand::{rand128bits, rand64bits},
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
    pub fn __new__(_py_args: &Bound<'_, PyTuple>, _py_kwargs: Option<&Bound<'_, PyDict>>) -> Self {
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
    pub fn __new__(_py_args: &Bound<'_, PyTuple>, _py_kwargs: Option<&Bound<'_, PyDict>>) -> Self {
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

/// Extract PyBackedString from Python object, falling back to empty string on error.
///
/// Used for required properties (like `name`) which must always have a value.
/// Invalid types (int, float, list, etc.) are silently converted to "" rather than raising an error.
#[inline(always)]
fn extract_or_default<'a, 'py, T: Default + FromPyObject<'a, 'py>>(
    obj: &'a Bound<'py, PyAny>,
) -> T {
    obj.extract::<T>().unwrap_or_default()
}

/// Extract PyBackedString from Python object, falling back to None on error.
///
/// Used for optional properties (like `service`) which can be None.
/// Invalid types (int, float, list, etc.) are silently converted to None rather than raising an error.
#[inline(always)]
fn extract_backed_string_or_none(obj: &Bound<'_, PyAny>) -> PyBackedString {
    let py = obj.py();
    obj.extract::<PyBackedString>()
        .unwrap_or_else(|_| PyBackedString::py_none(py))
}

#[pyo3::pymethods]
impl SpanData {
    #[new]
    #[allow(unused_variables)]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        name, service=None,
        trace_id = None,
        span_id = None,
        parent_id = None,
        *args,
        **kwargs,
    ))]
    pub fn __new__<'p>(
        py: Python<'p>,
        name: &Bound<'p, PyAny>,
        service: Option<&Bound<'p, PyAny>>,
        trace_id: Option<&Bound<'p, PyAny>>,
        span_id: Option<&Bound<'p, PyAny>>,
        parent_id: Option<&Bound<'p, PyAny>>,
        // Accept *args/**kwargs so subclasses don't need to override __new__
        args: &Bound<'p, PyTuple>,
        kwargs: Option<&Bound<'p, PyDict>>,
    ) -> Self {
        let mut span = Self::default();
        let span_id: u64 = span_id
            .and_then(extract_or_default)
            .unwrap_or_else(rand64bits);
        let trace_id: u128 = trace_id
            .and_then(extract_or_default)
            .unwrap_or_else(rand128bits);
        let parent_id: u64 = parent_id.and_then(extract_or_default).unwrap_or(0);

        span.set_name(name);
        match service {
            Some(obj) => span.set_service(obj),
            None => span.data.service = PyBackedString::py_none(py),
        }
        span.data.span_id = span_id;
        span.data.trace_id = trace_id;
        span.data.parent_id = parent_id;
        span
    }

    #[getter]
    #[inline(always)]
    fn get_name<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        // Use as_py to handle both stored (zero-copy) and static (interned) strings
        self.data.name.as_py(py)
    }

    #[setter]
    #[inline(always)]
    fn set_name(&mut self, name: &Bound<'_, PyAny>) {
        self.data.name = extract_or_default(name);
    }

    #[getter]
    #[inline(always)]
    fn get_service<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyAny>> {
        // Return None for Python None, otherwise return the string (stored or interned)
        if self.data.service.is_py_none(py) {
            None
        } else {
            Some(self.data.service.as_py(py))
        }
    }

    #[setter]
    #[inline(always)]
    fn set_service(&mut self, service: &Bound<'_, PyAny>) {
        self.data.service = extract_backed_string_or_none(service);
    }

    #[getter]
    fn get_span_id(&self) -> u64 {
        self.data.span_id
    }

    #[setter]
    fn set_span_id(&mut self, span_id: &Bound<'_, PyAny>) {
        if let Some(span_id) = extract_or_default(span_id) {
            self.data.span_id = span_id
        }
    }

    #[getter]
    fn get_trace_id(&self) -> u128 {
        self.data.trace_id
    }

    #[setter]
    fn set_trace_id(&mut self, trace_id: &Bound<'_, PyAny>) {
        if let Some(trace_id) = extract_or_default(trace_id) {
            self.data.trace_id = trace_id
        }
    }

    #[getter]
    fn get_parent_id(&self) -> Option<u64> {
        if self.data.parent_id == 0 {
            None
        } else {
            Some(self.data.parent_id)
        }
    }

    #[setter]
    fn set_parent_id(&mut self, parent_id: &Bound<'_, PyAny>) {
        self.data.parent_id = extract_or_default(parent_id);
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanLinkData>()?;
    m.add_class::<SpanEventData>()?;
    m.add_class::<SpanData>()?;
    Ok(())
}
