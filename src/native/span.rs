
use pyo3::{
    types::{PyAnyMethods as _, PyDict, PyDictMethods as _, PyFloat, PyInt, PyModule, PyModuleMethods as _, PyTuple},
    Bound, PyAny, PyResult, Python,
};
use std::time::SystemTime;

use crate::py_string::{PyBackedString, PyTraceData};
use libdd_trace_utils::span::SpanText;

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

    // --- Attribute write methods ---

    /// Set a string attribute. Silently drops if key or value are not strings.
    /// Enforces mutual exclusion with metrics (removes same key from metrics).
    #[pyo3(name = "_set_str_attribute")]
    fn set_str_attribute(&mut self, key: Bound<'_, PyAny>, value: Bound<'_, PyAny>) {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return;
        };
        let Ok(value_bs) = value.extract::<PyBackedString>() else {
            return;
        };
        self.data.metrics.remove(&*key_bs); // mutual exclusion
        self.data.meta.insert(key_bs, value_bs);
    }

    /// Set a numeric attribute. Silently drops if key is not a string or value is not numeric.
    /// Enforces mutual exclusion with meta (removes same key from meta).
    #[pyo3(name = "_set_numeric_attribute")]
    fn set_numeric_attribute(&mut self, key: Bound<'_, PyAny>, value: Bound<'_, PyAny>) {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return;
        };
        let Ok(val_f64) = value.extract::<f64>() else {
            return;
        };
        self.data.meta.remove(&*key_bs); // mutual exclusion
        self.data.metrics.insert(key_bs, val_f64);
    }

    /// Remove an attribute from both meta and metrics.
    #[pyo3(name = "_remove_attribute")]
    fn remove_attribute(&mut self, key: Bound<'_, PyAny>) {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return;
        };
        self.data.meta.remove(&*key_bs);
        self.data.metrics.remove(&*key_bs);
    }

    /// Set an attribute dispatching on type: str → meta, int/float → metrics.
    /// Unrecognized types are stringified via __str__ and stored in meta.
    #[pyo3(name = "_set_attribute")]
    fn set_attribute(&mut self, key: Bound<'_, PyAny>, value: Bound<'_, PyAny>) {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return;
        };
        // Try string first (most common case)
        if let Ok(s) = value.extract::<PyBackedString>() {
            self.data.metrics.remove(&*key_bs);
            self.data.meta.insert(key_bs, s);
            return;
        }
        // Try numeric (int or float → f64)
        if let Ok(val) = value.extract::<f64>() {
            self.data.meta.remove(&*key_bs);
            self.data.metrics.insert(key_bs, val);
            return;
        }
        // Unrecognized type: stringify and store in meta
        if let Ok(s) = value.str().and_then(|s| s.extract::<PyBackedString>()) {
            self.data.metrics.remove(&*key_bs);
            self.data.meta.insert(key_bs, s);
        }
    }

    /// Set multiple attributes from a dict.
    #[pyo3(name = "_set_attributes")]
    fn set_attributes(&mut self, attrs: Bound<'_, PyAny>) {
        let Ok(dict) = attrs.cast::<PyDict>() else {
            return;
        };
        for (key, value) in dict.iter() {
            let Ok(key_bs) = key.extract::<PyBackedString>() else {
                continue;
            };
            if let Ok(s) = value.extract::<PyBackedString>() {
                self.data.metrics.remove(&*key_bs);
                self.data.meta.insert(key_bs, s);
            } else if let Ok(val) = value.extract::<f64>() {
                self.data.meta.remove(&*key_bs);
                self.data.metrics.insert(key_bs, val);
            } else if let Ok(s) = value.str().and_then(|s| s.extract::<PyBackedString>()) {
                self.data.metrics.remove(&*key_bs);
                self.data.meta.insert(key_bs, s);
            }
        }
    }

    // --- Attribute read methods ---

    /// Get an attribute by key, checking meta first then metrics.
    /// Returns str if found in meta, float if found in metrics, None if not found.
    #[pyo3(name = "_get_attribute")]
    fn get_attribute<'py>(&self, py: Python<'py>, key: Bound<'_, PyAny>) -> Option<Bound<'py, PyAny>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return None;
        };
        if let Some(v) = self.data.meta.get(&*key_bs) {
            return Some(v.as_py(py));
        }
        if let Some(&v) = self.data.metrics.get(&*key_bs) {
            return Some(PyFloat::new(py, v).into_any());
        }
        None
    }

    /// Get a string attribute by key. Returns None if not found.
    #[pyo3(name = "_get_str_attribute")]
    fn get_str_attribute<'py>(&self, py: Python<'py>, key: Bound<'_, PyAny>) -> Option<Bound<'py, PyAny>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return None;
        };
        self.data.meta.get(&*key_bs).map(|v| v.as_py(py))
    }

    /// Get a numeric attribute by key. Always returns float. Returns None if not found.
    #[pyo3(name = "_get_numeric_attribute")]
    fn get_numeric_attribute<'py>(&self, py: Python<'py>, key: Bound<'_, PyAny>) -> Option<Bound<'py, PyFloat>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return None;
        };
        self.data.metrics.get(&*key_bs).map(|&v| PyFloat::new(py, v))
    }

    /// Check if an attribute exists in either meta or metrics.
    #[pyo3(name = "_has_attribute")]
    fn has_attribute(&self, key: Bound<'_, PyAny>) -> bool {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return false;
        };
        self.data.meta.contains_key(&*key_bs) || self.data.metrics.contains_key(&*key_bs)
    }

    // --- Bulk read methods ---

    /// Return all string attributes as a Python dict.
    #[pyo3(name = "_get_str_attributes")]
    fn get_str_attributes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        for (k, v) in self.data.meta.iter() {
            dict.set_item(k.as_py(py), v.as_py(py))?;
        }
        Ok(dict)
    }

    /// Return all numeric attributes as a Python dict (values are float).
    #[pyo3(name = "_get_numeric_attributes")]
    fn get_numeric_attributes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let dict = PyDict::new(py);
        for (k, v) in self.data.metrics.iter() {
            dict.set_item(k.as_py(py), *v)?;
        }
        Ok(dict)
    }

    // --- Read-only _meta / _metrics properties (aliases) ---

    /// Read-only property returning all string attributes as a Python dict.
    #[getter(_meta)]
    fn get_meta<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        self.get_str_attributes(py)
    }

    /// Read-only property returning all numeric attributes as a Python dict.
    #[getter(_metrics)]
    fn get_metrics_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        self.get_numeric_attributes(py)
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanLinkData>()?;
    m.add_class::<SpanEventData>()?;
    m.add_class::<SpanData>()?;
    Ok(())
}
