use std::{
    borrow::Borrow,
    collections::HashMap,
    hash::Hash,
    ops::Deref,
    ptr::NonNull,
    str,
    time::{Duration, SystemTime},
};

use datadog_trace_utils::span::{Span as NativeSpan, SpanText};

use pyo3::{
    exceptions::{PyTypeError, PyValueError},
    types::{
        IntoPyDict, PyAny, PyAnyMethods, PyBytesMethods, PyDict, PyFloat, PyFloatMethods, PyInt,
        PyModule, PyModuleMethods, PyString, PyStringMethods,
    },
    Bound, FromPyObject, Py, PyErr, PyResult, Python,
};

use crate::rand::{rand128bits, rand64bits};

/// A Py bytes backed utf-8 string we can read without needing access to the GIL
struct PyBackedString {
    data: NonNull<str>,
    #[allow(unused)]
    storage: Option<Py<PyAny>>,
}

// Py bytes as immutable and can thus be safely shared between threads
unsafe impl Sync for PyBackedString {}
unsafe impl Send for PyBackedString {}

impl TryFrom<pyo3::Bound<'_, pyo3::types::PyString>> for PyBackedString {
    type Error = pyo3::PyErr;
    fn try_from(py_string: pyo3::Bound<'_, pyo3::types::PyString>) -> Result<Self, Self::Error> {
        let s = py_string.to_str()?;
        let data = NonNull::from(s);
        Ok(Self {
            storage: Some(py_string.unbind().into_any()),
            data,
        })
    }
}

impl TryFrom<pyo3::Bound<'_, pyo3::types::PyBytes>> for PyBackedString {
    type Error = pyo3::PyErr;
    fn try_from(py_bytes: pyo3::Bound<'_, pyo3::types::PyBytes>) -> Result<Self, Self::Error> {
        let s = std::str::from_utf8(py_bytes.as_bytes())
            .map_err(|_e| pyo3::PyErr::new::<PyValueError, _>("'bytes' are not utf8 encoded"))?;
        let data = NonNull::from(s);
        Ok(Self {
            storage: Some(py_bytes.unbind().into_any()),
            data,
        })
    }
}

impl pyo3::FromPyObject<'_> for PyBackedString {
    fn extract_bound(obj: &pyo3::Bound<'_, PyAny>) -> pyo3::PyResult<Self> {
        if let Ok(py_string) = obj.downcast::<pyo3::types::PyString>() {
            return Self::try_from(py_string.to_owned());
        }
        if let Ok(py_bytes) = obj.downcast::<pyo3::types::PyBytes>() {
            return Self::try_from(py_bytes.to_owned());
        }
        if let Ok(py_none) = obj.downcast_exact::<pyo3::types::PyNone>() {
            return Ok(Self {
                data: unsafe { NonNull::new_unchecked("" as *const str as *mut _) },
                storage: Some(py_none.to_owned().unbind().into_any()),
            });
        }
        Err(PyErr::new::<PyValueError, _>(
            "argument needs to be either a 'str', uft8 encoded 'bytes', or 'None'",
        ))
    }
}

impl<'py> pyo3::IntoPyObject<'py> for &PyBackedString {
    type Target = pyo3::types::PyAny;

    type Output = pyo3::Bound<'py, Self::Target>;

    type Error = std::convert::Infallible;

    fn into_pyobject(self, py: pyo3::Python<'py>) -> Result<Self::Output, Self::Error> {
        Ok(match &self.storage {
            Some(python_str) => python_str.bind(py).to_owned(),
            None => PyString::new(py, self.deref()).into_any(),
        })
    }
}

impl Deref for PyBackedString {
    type Target = str;

    fn deref(&self) -> &Self::Target {
        unsafe { self.data.as_ref() }
    }
}

impl Hash for PyBackedString {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.deref().hash(state);
    }
}

impl serde::Serialize for PyBackedString {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        self.deref().serialize(serializer)
    }
}

impl Borrow<str> for PyBackedString {
    fn borrow(&self) -> &str {
        self.deref()
    }
}

impl PartialEq for PyBackedString {
    fn eq(&self, other: &Self) -> bool {
        self.deref() == other.deref()
    }
}

impl Eq for PyBackedString {}

impl Default for PyBackedString {
    fn default() -> Self {
        Self::from_static_str("")
    }
}

impl SpanText for PyBackedString {
    fn from_static_str(value: &'static str) -> Self {
        Self {
            data: unsafe { NonNull::new_unchecked(value as *const str as *mut _) },
            storage: None,
        }
    }
}

#[pyo3::pyclass(name = "SpanData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
struct SpanData {
    data: NativeSpan<PyBackedString>,
    // Store duration here until the span is finished
    duration_ns: Option<i64>,
    #[pyo3(get, set)]
    _span_api: PyBackedString,
}

fn time_ns() -> i64 {
    let now = SystemTime::now();
    match now.duration_since(SystemTime::UNIX_EPOCH) {
        Ok(d) => d.as_nanos() as i64,
        Err(e) => -(e.duration().as_nanos() as i64),
    }
}

/// Python can pass bigints to our function, like duration_ns and start_ns
/// These are in general edge cases which we won't be able to represent in the encoding format
/// anyway.
/// So we pick either the max/min value on over/underflow
enum OverflowInt {
    Ok(i64),
    Overflow,
    UnderFlow,
}

impl OverflowInt {
    fn saturate(&self) -> i64 {
        match self {
            OverflowInt::Ok(i) => *i,
            OverflowInt::Overflow => i64::MAX,
            OverflowInt::UnderFlow => i64::MIN,
        }
    }

    fn from_f64(f: f64) -> Self {
        if f > i64::MAX as f64 {
            Self::Overflow
        } else if f < i64::MIN as f64 {
            Self::UnderFlow
        } else {
            Self::Ok(f as i64)
        }
    }
}

impl<'py> FromPyObject<'py> for OverflowInt {
    fn extract_bound(ob: &Bound<'py, PyAny>) -> PyResult<Self> {
        let mut is_overflow = 0;
        let ret = unsafe {
            pyo3::ffi::PyLong_AsLongLongAndOverflow(ob.as_ptr(), &mut is_overflow as *mut _)
        };
        if ret == -1 {
            if let Some(err) = PyErr::take(ob.py()) {
                return Err(err);
            }
        }
        Ok(match is_overflow.cmp(&0) {
            std::cmp::Ordering::Greater => OverflowInt::Overflow,
            std::cmp::Ordering::Equal => OverflowInt::Ok(ret as i64),
            std::cmp::Ordering::Less => OverflowInt::UnderFlow,
        })
    }
}

fn f64_unix_secs_to_nanos_saturate(unix_secs: f64) -> i64 {
    OverflowInt::from_f64(unix_secs * 1_000_000_000.0).saturate()
}

#[pyo3::pymethods]
impl SpanData {
    #[new]
    #[pyo3(signature =(
        *_py_args,
        **_py_kwargs,
    ))]
    #[allow(clippy::too_many_arguments)]
    fn __new__<'py>(
        _py_args: &pyo3::Bound<'_, pyo3::types::PyTuple>,
        _py_kwargs: Option<&pyo3::Bound<'_, pyo3::types::PyDict>>,
    ) -> Self {
        Self::default()
    }

    /// Performs span initialization
    ///
    /// This can not be put on new, because otherwise the signature needs to match
    /// for every inherited class
    fn __init__(
        &mut self,
        name: PyBackedString,
        service: Option<PyBackedString>,
        resource: Option<PyBackedString>,
        span_type: Option<PyBackedString>,
        trace_id: Option<u128>,
        span_id: Option<u64>,
        parent_id: Option<u64>,
        start: Option<f64>,
        span_api: PyBackedString,
    ) {
        *self = Self {
            data: NativeSpan {
                name,
                service: service.unwrap_or_default(),
                resource: resource.unwrap_or_default(),
                r#type: span_type.unwrap_or_default(),
                trace_id: trace_id.unwrap_or_else(rand128bits),
                span_id: span_id.unwrap_or_else(rand64bits),
                parent_id: parent_id.unwrap_or(0),
                start: start
                    .map(f64_unix_secs_to_nanos_saturate)
                    .unwrap_or_else(time_ns),
                duration: 0,
                error: 0,
                meta: HashMap::default(),
                metrics: HashMap::default(),
                meta_struct: HashMap::default(),
                span_links: Vec::new(),
                span_events: Vec::new(),
            },
            duration_ns: None,
            _span_api: span_api,
        }
    }

    // setter/getters that map
    #[getter]
    #[allow(non_snake_case)]
    fn get__trace_id_64bits(&self) -> u64 {
        self.data.trace_id as u64
    }

    #[getter]
    fn get_finished(&self) -> bool {
        self.duration_ns.is_some()
    }

    #[getter]
    fn get_start(&self) -> f64 {
        self.data.start as f64 / 1_000_000_000.0
    }

    #[setter]
    fn set_start(&mut self, value: f64) {
        self.data.start = f64_unix_secs_to_nanos_saturate(value)
    }

    #[setter]
    fn set_finished(&mut self, value: bool) {
        if value {
            self.duration_ns = Some(
                match (SystemTime::UNIX_EPOCH + Duration::from_nanos(self.data.start as u64))
                    .elapsed()
                {
                    Ok(d) => d.as_nanos() as i64,
                    Err(e) => -(e.duration().as_nanos() as i64),
                },
            );
        } else {
            self.duration_ns = None
        }
    }

    fn _set_default_metrics_inner<'p>(
        &mut self,
        tag_name: PyBackedString,
        value: &Bound<'p, PyAny>,
    ) -> PyResult<()> {
        match self.data.metrics.entry(tag_name) {
            std::collections::hash_map::Entry::Occupied(_) => {}
            std::collections::hash_map::Entry::Vacant(vacant_entry) => {
                let value = if let Ok(d) = value.downcast_exact::<PyFloat>() {
                    d.value() as f64
                } else if let Ok(d) = value.downcast_exact::<PyInt>() {
                    d.extract::<f64>()?
                } else {
                    return Err(PyErr::new::<PyTypeError, _>(
                        "span 'metrics' value should be either 'int' or 'float'",
                    ));
                };
                vacant_entry.insert(value);
            }
        }
        Ok(())
    }

    fn _set_metrics_inner<'p>(
        &mut self,
        tag_name: PyBackedString,
        value: &Bound<'p, PyAny>,
    ) -> PyResult<()> {
        let Ok(val) = value.extract::<f64>() else {
            return Err(PyErr::new::<PyTypeError, _>(
                "span 'metrics' value should be either 'int' or 'float'",
            ));
        };
        self.data.metrics.insert(tag_name, val);
        Ok(())
    }

    fn _delete_metrics_inner(&mut self, tag_name: PyBackedString) {
        self.data.metrics.remove(&tag_name);
    }

    fn _get_metrics_inner(&self, tag_name: PyBackedString) -> Option<f64> {
        self.data.metrics.get(&tag_name).copied()
    }

    fn _metrics_into_py_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        self.data.metrics.into_py_dict(py)
    }

    #[getter]
    fn get_duration(&self) -> Option<f64> {
        Some(self.duration_ns? as f64 / 1_000_000_000.0)
    }

    #[setter]
    fn set_duration(&mut self, value: f64) {
        self.duration_ns = Some(f64_unix_secs_to_nanos_saturate(value))
    }

    // setter/getters for scalar properties
    #[getter]
    fn get_service(&self) -> &PyBackedString {
        &self.data.service
    }

    #[setter]
    fn set_service(&mut self, service: PyBackedString) {
        self.data.service = service;
    }

    #[getter]
    fn get_name(&self) -> &PyBackedString {
        &self.data.name
    }

    #[setter]
    fn set_name(&mut self, name: PyBackedString) {
        self.data.name = name;
    }

    #[getter]
    fn get_resource(&self) -> &PyBackedString {
        &self.data.resource
    }

    #[setter]
    fn set_resource(&mut self, resource: PyBackedString) {
        self.data.resource = resource;
    }

    #[getter]
    fn get_span_type(&self) -> &PyBackedString {
        &self.data.r#type
    }

    #[setter]
    fn set_span_type(&mut self, span_type: PyBackedString) {
        self.data.r#type = span_type;
    }

    #[getter]
    fn get_trace_id(&self) -> u128 {
        self.data.trace_id
    }

    #[setter]
    fn set_trace_id(&mut self, trace_id: u128) {
        self.data.trace_id = trace_id;
    }

    #[getter]
    fn get_span_id(&self) -> u64 {
        self.data.span_id
    }

    #[setter]
    fn set_span_id(&mut self, span_id: u64) {
        self.data.span_id = span_id;
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

    #[getter]
    fn get_start_ns(&self) -> i64 {
        self.data.start
    }

    #[setter]
    fn set_start_ns(&mut self, start: i64) {
        self.data.start = start;
    }

    #[getter]
    fn get_error(&self) -> i32 {
        self.data.error
    }

    #[setter]
    fn set_error(&mut self, error: i32) {
        self.data.error = error;
    }

    #[getter]
    fn get_duration_ns(&self) -> Option<i64> {
        self.duration_ns
    }

    #[setter]
    fn set_duration_ns(&mut self, value: Option<OverflowInt>) {
        self.duration_ns = value.as_ref().map(OverflowInt::saturate);
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanData>()?;
    Ok(())
}
