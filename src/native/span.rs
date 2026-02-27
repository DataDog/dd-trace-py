use pyo3::{
    types::{
        PyAnyMethods as _, PyDict, PyDictMethods as _, PyFloat, PyInt, PyMapping,
        PyMappingMethods as _, PyModule, PyModuleMethods as _, PyTuple,
    },
    Bound, IntoPyObject as _, Py, PyAny, PyResult, Python,
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
pub struct SpanData {
    pub(crate) data: libdd_trace_utils::span::v04::Span<PyTraceData>,
    span_api: PyBackedString,
    /// Lazy Python int cache for the `trace_id` getter.
    /// Populated on first read; invalidated on every write to `data.trace_id`.
    /// `data.trace_id` is always the source of truth.
    _trace_id_py: Option<Py<PyAny>>,
    _meta: Py<PyDict>,
    _metrics: Py<PyDict>,
}

impl SpanData {
    /// Set `data.trace_id` and invalidate `_trace_id_py`.
    ///
    /// **All writes to `data.trace_id` must go through this method** to keep `_trace_id_py`
    /// consistent. Bypassing it leaves a stale cached Python int that silently returns the
    /// old value on the next `span.trace_id` read.
    #[inline(always)]
    fn set_trace_id_native(&mut self, id: u128) {
        self.data.trace_id = id;
        self._trace_id_py = None;
    }

    /// Insert a string attribute, enforcing mutual exclusion with metrics.
    #[inline(always)]
    pub(crate) fn meta_insert(&mut self, key: PyBackedString, value: PyBackedString) {
        self.data.metrics.remove(&*key);
        self._metrics.del_item(key);
        self.data.meta.insert(key, value);
        self._meta.set_item(key, value).ok();
    }

    /// Insert a numeric attribute, enforcing mutual exclusion with meta.
    #[inline(always)]
    pub(crate) fn metrics_insert(&mut self, key: PyBackedString, value: f64) {
        self.data.meta.remove(&*key);
        self.data.metrics.insert(key, value);
    }

    /// Remove an attribute from both meta and metrics.
    #[inline(always)]
    pub(crate) fn attribute_remove(&mut self, key: &PyBackedString) {
        self.data.meta.remove(&**key);
        self.data.metrics.remove(&**key);
    }
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
        trace_id=None,
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
        trace_id: Option<&Bound<'p, PyAny>>,
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
        let mut span = Self {
            data: Default::default(),
            _trace_id_py: None,
            _meta: PyDict::new(py).into(),
            _metrics: PyDict::new(py).into(),
            span_api: span_api
                .map(|obj| extract_backed_string_or_default(obj))
                .unwrap_or_else(|| PyBackedString::from_static_str("datadog")),
        };

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
        // Initialize trace_id: use provided value, or generate based on 128-bit mode config.
        // When auto-generating, reads the Rust-owned AtomicBool set by Python Config.__init__:
        //   enabled  → generate_128bit_trace_id() (SystemTime upper bits + random lower bits)
        //   disabled → rand64bits() cast to u128   (random 64-bit value, upper bits zero)
        // The stored value is always the full intended ID; no masking is applied on reads.
        //
        // Optimization: when the caller passes a Python int, we seed `_trace_id_py` with it
        // directly.  This avoids allocating a brand-new PyLong when `span.trace_id` is first
        // read — the caller's object is already alive and can be reused.
        let trace_id_cached = match trace_id {
            Some(obj) => match obj.extract::<u128>() {
                Ok(id) => {
                    span.set_trace_id_native(id);
                    // Seed the cache with the caller-provided Python int.
                    Some(obj.clone().unbind())
                }
                Err(_) => {
                    // Invalid type — fall through to auto-generation.
                    let id = if crate::config::get_128_bit_trace_id_enabled() {
                        crate::rand::generate_128bit_trace_id()
                    } else {
                        crate::rand::rand64bits() as u128
                    };
                    span.set_trace_id_native(id);
                    None
                }
            },
            None => {
                let id = if crate::config::get_128_bit_trace_id_enabled() {
                    crate::rand::generate_128bit_trace_id()
                } else {
                    crate::rand::rand64bits() as u128
                };
                span.set_trace_id_native(id);
                None
            }
        };
        // Override the None left by set_trace_id_native with the pre-seeded cache (if any).
        span._trace_id_py = trace_id_cached;
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

    // trace_id property - returns the stored trace_id as-is
    #[getter]
    #[inline(always)]
    fn get_trace_id<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyAny> {
        // Lazy-init: create the Python int on first read, reuse on subsequent reads.
        // Invalidated (set to None) on every write to data.trace_id.
        // data.trace_id is always the source of truth; _trace_id_py is purely a Python-side cache.
        if self._trace_id_py.is_none() {
            let val = self.data.trace_id;
            // SAFETY: u128 can always be converted to a Python int
            self._trace_id_py = Some(
                val.into_pyobject(py)
                    .expect("u128 into_pyobject")
                    .into_any()
                    .unbind(),
            );
        }
        // SAFETY: guaranteed Some above
        self._trace_id_py.as_ref().unwrap().bind(py).clone()
    }

    #[setter]
    #[inline(always)]
    fn set_trace_id(&mut self, value: &Bound<'_, PyAny>) {
        // Extract u128, silently ignore invalid types (keep existing value)
        if let Ok(id) = value.extract::<u128>() {
            self.set_trace_id_native(id);
        }
    }

    // _trace_id_64bits property - always returns lower 64 bits
    #[getter]
    #[inline(always)]
    #[allow(non_snake_case)]
    fn get__trace_id_64bits(&self) -> u64 {
        (self.data.trace_id & 0xFFFF_FFFF_FFFF_FFFF) as u64
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
        self.meta_insert(key_bs, value_bs);
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
        self.metrics_insert(key_bs, val_f64);
    }

    /// Remove an attribute from both meta and metrics.
    #[pyo3(name = "_remove_attribute")]
    fn remove_attribute(&mut self, key: Bound<'_, PyAny>) {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return;
        };
        self.attribute_remove(&key_bs);
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
            self.meta_insert(key_bs, s);
            return;
        }
        // Try numeric (int or float → f64)
        if let Ok(val) = value.extract::<f64>() {
            self.metrics_insert(key_bs, val);
            return;
        }
        // Unrecognized type: stringify and store in meta
        if let Ok(s) = value.str().and_then(|s| s.extract::<PyBackedString>()) {
            self.meta_insert(key_bs, s);
        }
    }

    /// Set multiple attributes from any Mapping (dict, StrAttributesMapping, etc.).
    #[pyo3(name = "_set_attributes")]
    fn set_attributes(&mut self, attrs: Bound<'_, PyAny>) {
        let Ok(mapping) = attrs.cast::<PyMapping>() else {
            return;
        };
        let Ok(items) = mapping.items() else {
            return;
        };
        let Ok(iter) = items.into_any().try_iter() else {
            return;
        };
        for item in iter {
            let Ok(item) = item else { continue };
            let Ok((key, value)) = item.extract::<(Bound<'_, PyAny>, Bound<'_, PyAny>)>() else {
                continue;
            };
            let Ok(key_bs) = key.extract::<PyBackedString>() else {
                continue;
            };
            if let Ok(s) = value.extract::<PyBackedString>() {
                self.meta_insert(key_bs, s);
            } else if let Ok(val) = value.extract::<f64>() {
                self.metrics_insert(key_bs, val);
            } else if let Ok(s) = value.str().and_then(|s| s.extract::<PyBackedString>()) {
                self.meta_insert(key_bs, s);
            }
        }
    }

    // --- Attribute read methods ---

    /// Get an attribute by key, checking meta first then metrics.
    /// Returns str if found in meta, float if found in metrics, None if not found.
    #[pyo3(name = "_get_attribute")]
    fn get_attribute<'py>(
        &self,
        py: Python<'py>,
        key: Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
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
    fn get_str_attribute<'py>(
        &self,
        py: Python<'py>,
        key: Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return None;
        };
        self.data.meta.get(&*key_bs).map(|v| v.as_py(py))
    }

    /// Get a numeric attribute by key. Always returns float. Returns None if not found.
    #[pyo3(name = "_get_numeric_attribute")]
    fn get_numeric_attribute<'py>(
        &self,
        py: Python<'py>,
        key: Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyFloat>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return None;
        };
        self.data
            .metrics
            .get(&*key_bs)
            .map(|&v| PyFloat::new(py, v))
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

    /// Return all string attributes as a zero-copy Mapping view over the internal HashMap.
    ///
    /// The returned `StrAttributesMapping` holds a reference to this span and reads
    /// directly from `data.meta` on each access. `__len__` and `__bool__` are O(1).
    /// `items()` returns a `PyList` of `(key, value)` tuples with near-zero-copy keys/values
    /// (PyBackedString.as_py() is just a Py_INCREF).
    #[pyo3(name = "_get_str_attributes")]
    fn get_str_attributes(slf: Bound<'_, Self>) -> PyDict {
        return self._meta.clone_ref(slf.py());
    }

    /// Return all numeric attributes as a zero-copy Mapping view over the internal HashMap.
    ///
    /// The returned `NumericAttributesMapping` holds a reference to this span and reads
    /// directly from `data.metrics` on each access. Values are `PyFloat` (unavoidable allocation).
    #[pyo3(name = "_get_numeric_attributes")]
    fn get_numeric_attributes(slf: Bound<'_, Self>) -> PyDict {
        return self._metrics.clone_ref(slf.py());
    }
}

pub fn register_native_span(m: &pyo3::Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SpanLinkData>()?;
    m.add_class::<SpanEventData>()?;
    m.add_class::<SpanData>()?;
    Ok(())
}
