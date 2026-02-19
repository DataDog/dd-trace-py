use pyo3::{
    exceptions::PyKeyError,
    types::{
        PyAnyMethods as _, PyBool, PyDict, PyDictMethods as _, PyFloat, PyInt, PyList,
        PyListMethods as _, PyModule, PyModuleMethods as _, PyTuple,
    },
    Bound, IntoPyObject, Py, PyAny, PyResult, Python,
};
use std::collections::HashMap;
use std::ops::Deref;
use std::time::SystemTime;

use crate::py_string::{PyBackedString, PyTraceData};
use libdd_trace_utils::span::SpanText;
use libdd_trace_utils::span::v04::{
    AttributeAnyValue as LibAttributeAnyValue, AttributeArrayValue as LibAttributeArrayValue,
    SpanEvent as LibSpanEvent,
};

/// Try to get a string from a Python object:
/// 1. If it's already a str, extract directly
/// 2. Otherwise, call Python's str() to convert
/// 3. Return Err if str() itself fails
fn try_stringify(obj: &Bound<'_, PyAny>) -> PyResult<PyBackedString> {
    // Fast path: already a string
    if let Ok(s) = obj.extract::<PyBackedString>() {
        return Ok(s);
    }
    // Slow path: call str(obj)
    let py_str = obj.str()?;
    py_str.extract::<PyBackedString>()
}

// bool must be checked before int (Python bool subclasses int).
fn py_to_array_value(
    obj: &Bound<'_, PyAny>,
) -> PyResult<LibAttributeArrayValue<PyTraceData>> {
    if obj.is_instance_of::<PyBool>() {
        let b: bool = obj.extract()?;
        return Ok(LibAttributeArrayValue::Boolean(b));
    }
    if let Ok(s) = obj.extract::<PyBackedString>() {
        return Ok(LibAttributeArrayValue::String(s));
    }
    if obj.is_instance_of::<PyInt>() {
        let i: i64 = obj.extract()?;
        return Ok(LibAttributeArrayValue::Integer(i));
    }
    if obj.is_instance_of::<PyFloat>() {
        let f: f64 = obj.extract()?;
        return Ok(LibAttributeArrayValue::Double(f));
    }
    Ok(LibAttributeArrayValue::String(
        try_stringify(obj).unwrap_or_default(),
    ))
}

fn py_to_attribute_value(
    obj: &Bound<'_, PyAny>,
) -> PyResult<LibAttributeAnyValue<PyTraceData>> {
    if obj.is_instance_of::<PyBool>() {
        let b: bool = obj.extract()?;
        return Ok(LibAttributeAnyValue::SingleValue(
            LibAttributeArrayValue::Boolean(b),
        ));
    }
    if let Ok(s) = obj.extract::<PyBackedString>() {
        return Ok(LibAttributeAnyValue::SingleValue(
            LibAttributeArrayValue::String(s),
        ));
    }
    if obj.is_instance_of::<PyInt>() {
        let i: i64 = obj.extract()?;
        return Ok(LibAttributeAnyValue::SingleValue(
            LibAttributeArrayValue::Integer(i),
        ));
    }
    if obj.is_instance_of::<PyFloat>() {
        let f: f64 = obj.extract()?;
        return Ok(LibAttributeAnyValue::SingleValue(
            LibAttributeArrayValue::Double(f),
        ));
    }
    if let Ok(list) = obj.downcast::<PyList>() {
        let mut values = Vec::with_capacity(list.len());
        for item in list.iter() {
            values.push(py_to_array_value(&item)?);
        }
        return Ok(LibAttributeAnyValue::Array(values));
    }
    Ok(LibAttributeAnyValue::SingleValue(
        LibAttributeArrayValue::String(try_stringify(obj).unwrap_or_default()),
    ))
}

fn py_dict_to_attributes(
    dict: &Bound<'_, PyDict>,
) -> PyResult<HashMap<PyBackedString, LibAttributeAnyValue<PyTraceData>>> {
    let mut result = HashMap::with_capacity(dict.len());
    for (k, v) in dict.iter() {
        let key = try_stringify(&k).unwrap_or_default();
        let val = py_to_attribute_value(&v)?;
        result.insert(key, val);
    }
    Ok(result)
}

fn array_value_to_py(
    py: Python<'_>,
    val: &LibAttributeArrayValue<PyTraceData>,
) -> PyResult<Py<PyAny>> {
    match val {
        LibAttributeArrayValue::String(s) => Ok(s.as_py(py).unbind()),
        LibAttributeArrayValue::Boolean(b) => {
            Ok((*pyo3::types::PyBool::new(py, *b)).clone().into_any().unbind())
        }
        LibAttributeArrayValue::Integer(i) => Ok(i.into_pyobject(py)?.into_any().unbind()),
        LibAttributeArrayValue::Double(f) => Ok(f.into_pyobject(py)?.into_any().unbind()),
    }
}

fn attribute_value_to_py(
    py: Python<'_>,
    val: &LibAttributeAnyValue<PyTraceData>,
) -> PyResult<Py<PyAny>> {
    match val {
        LibAttributeAnyValue::SingleValue(arr_val) => array_value_to_py(py, arr_val),
        LibAttributeAnyValue::Array(vec) => {
            let items: PyResult<Vec<Py<PyAny>>> =
                vec.iter().map(|v| array_value_to_py(py, v)).collect();
            Ok(PyList::new(py, items?)?.into_pyobject(py)?.into_any().unbind())
        }
    }
}

fn attribute_debug_repr(val: &LibAttributeAnyValue<PyTraceData>) -> String {
    match val {
        LibAttributeAnyValue::SingleValue(arr_val) => array_debug_repr(arr_val),
        LibAttributeAnyValue::Array(vec) => {
            let items: Vec<String> = vec.iter().map(array_debug_repr).collect();
            format!("[{}]", items.join(", "))
        }
    }
}

fn array_debug_repr(val: &LibAttributeArrayValue<PyTraceData>) -> String {
    match val {
        LibAttributeArrayValue::String(s) => format!("'{}'", s.deref()),
        LibAttributeArrayValue::Boolean(b) => {
            if *b {
                "True".to_owned()
            } else {
                "False".to_owned()
            }
        }
        LibAttributeArrayValue::Integer(i) => format!("{}", i),
        LibAttributeArrayValue::Double(f) => format!("{}", f),
    }
}

#[pyo3::pyclass(name = "SpanEvent", module = "ddtrace.internal._native")]
pub struct SpanEvent {
    data: LibSpanEvent<PyTraceData>,
}

#[pyo3::pymethods]
impl SpanEvent {
    #[new]
    #[pyo3(signature = (name, attributes = None, time_unix_nano = None))]
    pub fn __new__(
        name: &Bound<'_, PyAny>,
        attributes: Option<&Bound<'_, PyDict>>,
        time_unix_nano: Option<u64>,
    ) -> PyResult<Self> {
        let data_name = extract_backed_string_or_default(name);
        let data_time = time_unix_nano.unwrap_or_else(|| wall_clock_ns() as u64);
        let data_attributes = match attributes {
            None => HashMap::new(),
            Some(dict) => py_dict_to_attributes(dict)?,
        };
        Ok(Self {
            data: LibSpanEvent {
                name: data_name,
                time_unix_nano: data_time,
                attributes: data_attributes,
            },
        })
    }

    #[getter]
    #[inline(always)]
    fn get_name<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        self.data.name.as_py(py)
    }

    #[setter]
    #[inline(always)]
    fn set_name(&mut self, name: &Bound<'_, PyAny>) {
        self.data.name = extract_backed_string_or_default(name);
    }

    #[getter]
    #[inline(always)]
    fn get_time_unix_nano(&self) -> u64 {
        self.data.time_unix_nano
    }

    #[setter]
    #[inline(always)]
    fn set_time_unix_nano(&mut self, value: u64) {
        self.data.time_unix_nano = value;
    }

    #[getter]
    fn get_attributes(slf: Bound<'_, SpanEvent>) -> PyResult<Py<SpanEventAttributes>> {
        let py = slf.py();
        let parent = slf.unbind();
        Py::new(py, SpanEventAttributes { parent })
    }

    #[setter]
    fn set_attributes(&mut self, attributes: Option<&Bound<'_, PyDict>>) -> PyResult<()> {
        self.data.attributes = match attributes {
            None => HashMap::new(),
            Some(dict) => py_dict_to_attributes(dict)?,
        };
        Ok(())
    }

    pub fn to_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("name", self.data.name.as_py(py))?;
        dict.set_item("time_unix_nano", self.data.time_unix_nano)?;
        if !self.data.attributes.is_empty() {
            let attrs_dict = PyDict::new(py);
            for (k, v) in &self.data.attributes {
                attrs_dict.set_item(k.as_py(py), attribute_value_to_py(py, v)?)?;
            }
            dict.set_item("attributes", attrs_dict)?;
        }
        Ok(dict.into_any().unbind())
    }

    pub fn __repr__(&self) -> String {
        let attrs_repr = if self.data.attributes.is_empty() {
            "{}".to_owned()
        } else {
            let parts: Vec<String> = self
                .data
                .attributes
                .iter()
                .map(|(k, v)| format!("'{}': {}", k.deref(), attribute_debug_repr(v)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        };
        format!(
            "SpanEvent(name='{}', time={}, attributes={})",
            self.data.name.deref(),
            self.data.time_unix_nano,
            attrs_repr
        )
    }

    pub fn __iter__(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let name_tuple = PyTuple::new(
            py,
            [
                "name".into_pyobject(py)?.into_any(),
                self.data.name.as_py(py),
            ],
        )?;
        let time_tuple = PyTuple::new(
            py,
            [
                "time_unix_nano".into_pyobject(py)?.into_any(),
                self.data.time_unix_nano.into_pyobject(py)?.into_any(),
            ],
        )?;

        if self.data.attributes.is_empty() {
            let py_list = PyList::new(py, [name_tuple, time_tuple])?;
            return Ok(py_list.into_any().try_iter()?.unbind().into_any());
        }

        let attrs_dict = PyDict::new(py);
        for (k, v) in &self.data.attributes {
            attrs_dict.set_item(k.as_py(py), attribute_value_to_py(py, v)?)?;
        }
        let attrs_tuple = PyTuple::new(
            py,
            [
                "attributes".into_pyobject(py)?.into_any(),
                attrs_dict.into_any(),
            ],
        )?;
        let py_list = PyList::new(py, [name_tuple, time_tuple, attrs_tuple])?;
        Ok(py_list.into_any().try_iter()?.unbind().into_any())
    }
}

#[pyo3::pyclass(
    name = "SpanEventAttributes",
    module = "ddtrace.internal._native",
    mapping,
    frozen
)]
pub struct SpanEventAttributes {
    parent: Py<SpanEvent>,
}

#[pyo3::pymethods]
impl SpanEventAttributes {
    fn __len__(&self, py: Python<'_>) -> usize {
        self.parent.borrow(py).data.attributes.len()
    }

    fn __getitem__(&self, py: Python<'_>, key: &str) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        match parent.data.attributes.get(key) {
            Some(val) => attribute_value_to_py(py, val),
            None => Err(PyKeyError::new_err(key.to_owned())),
        }
    }

    fn __contains__(&self, py: Python<'_>, key: &str) -> bool {
        self.parent.borrow(py).data.attributes.contains_key(key)
    }

    fn __bool__(&self, py: Python<'_>) -> bool {
        !self.parent.borrow(py).data.attributes.is_empty()
    }

    fn __iter__(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        let keys: Vec<Py<PyAny>> = parent
            .data
            .attributes
            .keys()
            .map(|k| k.as_py(py).unbind())
            .collect();
        drop(parent);
        let py_list = PyList::new(py, keys)?;
        Ok(py_list.into_any().try_iter()?.unbind().into_any())
    }

    fn keys(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        let keys: Vec<Py<PyAny>> = parent
            .data
            .attributes
            .keys()
            .map(|k| k.as_py(py).unbind())
            .collect();
        drop(parent);
        Ok(PyList::new(py, keys)?.into_any().unbind())
    }

    fn values(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        let values: PyResult<Vec<Py<PyAny>>> = parent
            .data
            .attributes
            .values()
            .map(|v| attribute_value_to_py(py, v))
            .collect();
        drop(parent);
        Ok(PyList::new(py, values?)?.into_any().unbind())
    }

    fn items(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let parent = self.parent.borrow(py);
        let mut tuples: Vec<Py<PyAny>> = Vec::with_capacity(parent.data.attributes.len());
        for (k, v) in parent.data.attributes.iter() {
            let key = k.as_py(py).unbind();
            let val = attribute_value_to_py(py, v)?;
            let tup = PyTuple::new(py, [key, val])?;
            tuples.push(tup.into_any().unbind());
        }
        drop(parent);
        Ok(PyList::new(py, tuples)?.into_any().unbind())
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        let parent = self.parent.borrow(py);
        let parts: Vec<String> = parent
            .data
            .attributes
            .iter()
            .map(|(k, v)| format!("'{}': {}", k.deref(), attribute_debug_repr(v)))
            .collect();
        format!("{{{}}}", parts.join(", "))
    }

    fn __eq__(&self, py: Python<'_>, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        let parent = self.parent.borrow(py);
        let dict = PyDict::new(py);
        for (k, v) in parent.data.attributes.iter() {
            dict.set_item(k.as_py(py), attribute_value_to_py(py, v)?)?;
        }
        drop(parent);
        Ok(dict.into_any().eq(other)?)
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
    data: libdd_trace_utils::span::v04::Span<PyTraceData>,
    span_api: PyBackedString,
}

/// Extract PyBackedString from Python object, falling back to empty string on error.
///
/// Used for required properties (like `name`) which must always have a value.
/// Invalid types (int, float, list, etc.) are silently converted to "" rather than raising an error.
#[inline(always)]
fn extract_backed_string_or_default(obj: &Bound<'_, PyAny>) -> PyBackedString {
    obj.extract::<PyBackedString>().unwrap_or_default()
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

/// Extract i64 from Python object, falling back to 0 on error.
/// Accepts int or float (truncated).
#[inline(always)]
fn extract_i64_or_default(obj: &Bound<'_, PyAny>) -> i64 {
    obj.extract::<i64>()
        .or_else(|_| obj.extract::<f64>().map(|f| f as i64))
        .unwrap_or(0)
}

/// Extract i32 from Python object, falling back to 0 on error.
/// Note: Python bool subclasses int, so bools extract as 0/1 automatically.
#[inline(always)]
fn extract_i32_or_default(obj: &Bound<'_, PyAny>) -> i32 {
    obj.extract::<i32>().unwrap_or(0)
}

/// Get wall clock time in nanoseconds since Unix epoch.
/// Uses SystemTime for wall clock (matches Python's time.time_ns()).
#[inline(always)]
fn wall_clock_ns() -> i64 {
    SystemTime::UNIX_EPOCH
        .elapsed()
        .map(|d| d.as_nanos() as i64)
        .unwrap_or(0)
}

#[pyo3::pymethods]
impl SpanData {
    #[new]
    #[allow(unused_variables)]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        name,
        service=None,
        resource=None,
        span_type=None,
        trace_id=None,     // placeholder for Span.__init__ positional arg
        span_id=None,
        parent_id=None,
        start=None,
        context=None,      // placeholder for Span.__init__ positional arg
        on_finish=None,    // placeholder for Span.__init__ positional arg
        span_api=None,
        *args,
        **kwargs
    ))]
    pub fn __new__<'p>(
        py: Python<'p>,
        name: &Bound<'p, PyAny>,
        service: Option<&Bound<'p, PyAny>>,
        resource: Option<&Bound<'p, PyAny>>,
        span_type: Option<&Bound<'p, PyAny>>,
        trace_id: Option<&Bound<'p, PyAny>>, // placeholder, not used
        span_id: Option<&Bound<'p, PyAny>>,
        parent_id: Option<&Bound<'p, PyAny>>,
        start: Option<&Bound<'p, PyAny>>,
        context: Option<&Bound<'p, PyAny>>, // placeholder, not used
        on_finish: Option<&Bound<'p, PyAny>>, // placeholder, not used
        span_api: Option<&Bound<'p, PyAny>>,
        // Accept *args/**kwargs so subclasses don't need to override __new__
        args: &Bound<'p, PyTuple>,
        kwargs: Option<&Bound<'p, PyDict>>,
    ) -> Self {
        let mut span = Self::default();
        span.set_name(name);
        match service {
            Some(obj) => span.set_service(obj),
            // Directly set py_none to avoid creating a bound None and going through extraction
            None => span.data.service = PyBackedString::py_none(py),
        }
        // Set resource to the provided value, or default to name if None
        // Use clone_ref for efficient refcount increment with Python token
        match resource {
            Some(obj) => span.set_resource(obj),
            None => span.data.resource = span.data.name.clone_ref(py),
        }
        span.data.r#type = span_type
            .map(|obj| extract_backed_string_or_none(obj))
            .unwrap_or_else(|| PyBackedString::py_none(py));
        // Initialize parent_id: None or invalid → 0 (no parent), Some(int) → parent_id
        span.data.parent_id = parent_id
            .and_then(|obj| obj.extract::<u64>().ok())
            .unwrap_or(0);
        // Handle start parameter: None means capture current time, otherwise convert seconds to nanoseconds
        span.data.start = match start {
            None => wall_clock_ns(), // Common case: native time capture
            Some(obj) => {
                // start is in seconds (float or int), convert to nanoseconds
                obj.extract::<f64>()
                    .map(|s| (s * 1e9) as i64)
                    .or_else(|_| obj.extract::<i64>().map(|s| s * 1_000_000_000))
                    .unwrap_or_else(|_| wall_clock_ns()) // Invalid value: fall back to current time
            }
        };
        // Set duration to -1 (our sentinel for "not set")
        span.data.duration = -1;
        // Initialize span_id from parameter or generate random
        span.data.span_id = span_id
            .and_then(|obj| obj.extract::<u64>().ok())
            .unwrap_or_else(crate::rand::rand64bits);
        // Initialize span_api: use provided value or default to "datadog"
        span.span_api = span_api
            .map(|obj| extract_backed_string_or_default(obj))
            .unwrap_or_else(|| PyBackedString::from_static_str("datadog"));
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
        self.data.name = extract_backed_string_or_default(name);
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
    #[inline(always)]
    fn get_resource<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        // Use as_py to handle both stored (zero-copy) and static (interned) strings
        self.data.resource.as_py(py)
    }

    #[setter]
    #[inline(always)]
    fn set_resource(&mut self, resource: &Bound<'_, PyAny>) {
        self.data.resource = extract_backed_string_or_default(resource);
    }

    #[getter]
    #[inline(always)]
    fn get_span_type<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyAny>> {
        if self.data.r#type.is_py_none(py) {
            None
        } else {
            Some(self.data.r#type.as_py(py))
        }
    }

    #[setter]
    #[inline(always)]
    fn set_span_type(&mut self, span_type: &Bound<'_, PyAny>) {
        self.data.r#type = extract_backed_string_or_none(span_type);
    }

    // start_ns property (maps to self.data.start)
    #[getter]
    #[inline(always)]
    fn get_start_ns(&self) -> i64 {
        self.data.start
    }

    #[setter]
    #[inline(always)]
    fn set_start_ns(&mut self, value: &Bound<'_, PyAny>) {
        self.data.start = extract_i64_or_default(value);
    }

    // duration_ns property (maps to self.data.duration)
    // Returns None if duration is -1 (our sentinel for "not set"), else returns the value
    #[getter]
    #[inline(always)]
    fn get_duration_ns(&self) -> Option<i64> {
        if self.data.duration == -1 {
            None
        } else {
            Some(self.data.duration)
        }
    }

    #[setter]
    #[inline(always)]
    fn set_duration_ns(&mut self, value: Option<&Bound<'_, PyAny>>) {
        self.data.duration = match value {
            None => -1,
            Some(obj) => obj
                .extract::<i64>()
                .or_else(|_| obj.extract::<f64>().map(|f| f as i64))
                .unwrap_or(-1),
        };
    }

    // error property
    #[getter]
    #[inline(always)]
    fn get_error(&self) -> i32 {
        self.data.error
    }

    #[setter]
    #[inline(always)]
    fn set_error(&mut self, value: &Bound<'_, PyAny>) {
        self.data.error = extract_i32_or_default(value);
    }

    // span_id property
    #[getter]
    #[inline(always)]
    fn get_span_id(&self) -> u64 {
        self.data.span_id
    }

    #[setter]
    #[inline(always)]
    fn set_span_id(&mut self, value: &Bound<'_, PyAny>) {
        // Extract u64, silently ignore invalid types (keep existing value)
        if let Ok(id) = value.extract::<u64>() {
            self.data.span_id = id;
        }
    }

    // finished property (native for performance - avoids Python property hop)
    #[getter]
    #[inline(always)]
    fn get_finished(&self) -> bool {
        self.data.duration != -1
    }

    // start property - converts start_ns (nanoseconds) to seconds
    #[getter]
    #[inline(always)]
    fn get_start(&self) -> f64 {
        self.data.start as f64 / 1e9
    }

    #[setter]
    #[inline(always)]
    fn set_start(&mut self, value: &Bound<'_, PyAny>) {
        // Convert seconds to nanoseconds
        self.data.start = value
            .extract::<f64>()
            .map(|s| (s * 1e9) as i64)
            .or_else(|_| value.extract::<i64>().map(|s| s * 1_000_000_000))
            .unwrap_or(0);
    }

    // duration property - converts duration_ns (nanoseconds) to seconds
    // Returns None if duration is -1 (not set), else returns seconds as f64
    #[getter]
    #[inline(always)]
    fn get_duration(&self) -> Option<f64> {
        if self.data.duration == -1 {
            None
        } else {
            Some(self.data.duration as f64 / 1e9)
        }
    }

    #[setter]
    #[inline(always)]
    fn set_duration(&mut self, value: &Bound<'_, PyAny>) {
        // Convert seconds to nanoseconds
        self.data.duration = value
            .extract::<f64>()
            .map(|s| (s * 1e9) as i64)
            .or_else(|_| value.extract::<i64>().map(|s| s * 1_000_000_000))
            .unwrap_or(-1);
    }

    // parent_id property
    // Returns None if parent_id is 0 (no parent), else returns the value
    #[getter]
    #[inline(always)]
    fn get_parent_id(&self) -> Option<u64> {
        if self.data.parent_id == 0 {
            None
        } else {
            Some(self.data.parent_id)
        }
    }

    #[setter]
    #[inline(always)]
    fn set_parent_id(&mut self, value: Option<&Bound<'_, PyAny>>) {
        self.data.parent_id = match value {
            None => 0,
            Some(obj) => obj.extract::<u64>().unwrap_or(self.data.parent_id),
        };
    }

    // _span_api property
    #[getter(_span_api)]
    #[inline(always)]
    fn get_span_api<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        self.span_api.as_py(py)
    }

    #[setter(_span_api)]
    #[inline(always)]
    fn set_span_api(&mut self, value: &Bound<'_, PyAny>) {
        self.span_api = extract_backed_string_or_default(value);
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanLinkData>()?;
    m.add_class::<SpanEvent>()?;
    m.add_class::<SpanEventAttributes>()?;
    m.add_class::<SpanData>()?;
    // Register SpanEventAttributes as a collections.abc.Mapping
    pyo3::types::PyMapping::register::<SpanEventAttributes>(m.py())?;
    Ok(())
}
