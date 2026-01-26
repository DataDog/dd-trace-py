use pyo3::{
    exceptions::PyTypeError,
    types::{PyAnyMethods as _, PyInt, PyList, PyModule, PyModuleMethods as _},
    Bound, FromPyObject, Py, PyAny, PyResult, Python,
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
    pub fn __new__(
        _py_args: &pyo3::Bound<'_, pyo3::types::PyTuple>,
        _py_kwargs: Option<&pyo3::Bound<'_, pyo3::types::PyDict>>,
    ) -> Self {
        Self::default()
    }

    pub fn __init__(
        &mut self,
        _name: Py<PyAny>,
        _attributes: Option<Py<PyAny>>,
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
        tracestate: Option<Py<PyAny>>,
        flags: Option<&Bound<'p, PyInt>>,
        attributes: Option<Py<PyAny>>,
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
fn extract_backed_string_or_default(obj: &Bound<'_, PyAny>) -> PyBackedString {
    obj.extract::<PyBackedString>().unwrap_or_default()
}

/// Extract PyBackedString from Python object, falling back to None on error
fn extract_backed_string_or_none(obj: &Bound<'_, PyAny>) -> PyBackedString {
    let py = obj.py();
    obj.extract::<PyBackedString>()
        .unwrap_or_else(|_| PyBackedString::py_none(py))
}

fn number_from_optional<'a, 'py, T: FromPyObject<'a, 'py>>(
    v: Option<&'a Bound<'py, PyAny>>,
    err: &'static str,
) -> PyResult<Option<T>> {
    v.map(|s| s.extract().map_err(|_| PyTypeError::new_err(err)))
        .transpose()
}

impl SpanData {
    #[allow(unused_variables)]
    #[allow(clippy::too_many_arguments)]
    fn init_inner<'p>(
        &mut self,
        py: Python<'p>,
        name: Py<PyAny>,
        service: Option<Py<PyAny>>,
        resource: Option<Py<PyAny>>,
        span_type: Option<Py<PyAny>>,
        trace_id: Option<&Bound<'p, PyAny>>,
        span_id: Option<&Bound<'p, PyAny>>,
        parent_id: Option<&Bound<'p, PyAny>>,
        start: Option<f64>,
        span_api: Option<Py<PyAny>>,
        links: Option<Bound<'p, PyList>>,
        _128_bit_trace_id_enabled: bool,
    ) -> PyResult<()> {
        let span_id: u64 =
            number_from_optional(span_id, "span_id must be an integer")?.unwrap_or_else(rand64bits);
        let trace_id: u128 = number_from_optional(trace_id, "trace_id must be an integer")?
            .unwrap_or_else(|| {
                if _128_bit_trace_id_enabled {
                    rand128bits()
                } else {
                    rand64bits() as u128
                }
            });
        let parent_id: u64 =
            number_from_optional(parent_id, "trace_id must be an integer")?.unwrap_or(0);

        self.set_name(name.bind(py));
        match service {
            Some(obj) => self.set_service(obj.bind(py)),
            None => self.data.service = PyBackedString::py_none(py),
        }
        self.data.span_id = span_id;
        self.data.trace_id = trace_id;
        self.data.parent_id = parent_id;
        Ok(())
    }
}

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
        _raise = false,
        _128_bit_trace_id_enabled = true,
    ))]
    fn __init__<'p>(
        &mut self,
        py: Python<'p>,
        name: Py<PyAny>,
        service: Option<Py<PyAny>>,
        resource: Option<Py<PyAny>>,
        span_type: Option<Py<PyAny>>,
        trace_id: Option<&Bound<'p, PyAny>>,
        span_id: Option<&Bound<'p, PyAny>>,
        parent_id: Option<&Bound<'p, PyAny>>,
        start: Option<f64>,
        span_api: Option<Py<PyAny>>,
        links: Option<Bound<'p, PyList>>,
        _raise: bool,
        _128_bit_trace_id_enabled: bool,
    ) -> PyResult<()> {
        let res = self.init_inner(
            py,
            name,
            service,
            resource,
            span_type,
            trace_id,
            span_id,
            parent_id,
            start,
            span_api,
            links,
            _128_bit_trace_id_enabled,
        );
        if _raise {
            res
        } else {
            Ok(())
        }
    }

    #[getter]
    fn get_name(&self) -> &PyBackedString {
        &self.data.name
    }

    #[setter]
    fn set_name(&mut self, name: &Bound<'_, PyAny>) {
        self.data.name = extract_backed_string_or_default(name);
    }

    #[getter]
    fn get_service(&self) -> &PyBackedString {
        &self.data.service
    }

    #[setter]
    fn set_service(&mut self, service: &Bound<'_, PyAny>) {
        self.data.service = extract_backed_string_or_none(service);
    }

    #[getter]
    fn get_span_id(&self) -> u64 {
        self.data.span_id
    }

    #[setter]
    fn set_span_id(&mut self, span_id: u64) {
        self.data.span_id = span_id
    }

    #[getter]
    fn get_trace_id(&self) -> u128 {
        self.data.trace_id
    }

    #[setter]
    fn set_trace_id(&mut self, trace_id: u128) {
        self.data.trace_id = trace_id
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
    fn set_parent_id(&mut self, parent_id: Option<u64>) {
        self.data.parent_id = parent_id.unwrap_or(0);
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanLinkData>()?;
    m.add_class::<SpanEventData>()?;
    m.add_class::<SpanData>()?;
    Ok(())
}
