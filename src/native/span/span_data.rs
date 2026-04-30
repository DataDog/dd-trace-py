use pyo3::{
    types::{
        PyAnyMethods as _, PyBool, PyBytes, PyBytesMethods as _, PyDict, PyDictMethods as _,
        PyFloat, PyFloatMethods as _, PyList, PyListMethods as _, PyMapping, PyMappingMethods as _,
        PyString, PyStringMethods as _, PyTuple,
    },
    Bound, IntoPyObject as _, Py, PyAny, PyResult, Python,
};

use super::attributes::{AttrKey, AttributeMap, AttributeValue};
use crate::py_string::{PyBackedString, PyTraceData};
use crate::utils::flatten_key_value_vec as flatten_key_value_vec_fn;
use libdd_trace_utils::span::{
    v04::{
        AttributeAnyValue, AttributeArrayValue, SpanEvent as NativeSpanEvent,
        SpanLink as NativeSpanLink,
    },
    SpanText as _,
};

use super::utils::{
    extract_backed_string_or_default, extract_backed_string_or_none, extract_i32_or_default,
    extract_i64_or_default, extract_time_unix_nano, wall_clock_ns,
};
use super::{SpanEvent, SpanLink};

#[pyo3::pyclass(name = "SpanData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct SpanData {
    pub data: libdd_trace_utils::span::v04::Span<PyTraceData>,
    pub span_api: PyBackedString,
    /// Unified attribute storage — source of truth for all tag/metric attributes.
    /// `data.meta` and `data.metrics` are left empty; they are materialized from
    /// this map at encode time (currently by the Python encoder via the bulk read
    /// accessors `_get_str_attributes` / `_get_numeric_attributes`).
    pub attributes: AttributeMap,
    /// Lazy Python int cache for the `trace_id` getter.
    /// Populated on first read; invalidated on every write to `data.trace_id`.
    /// `data.trace_id` is always the source of truth.
    pub _trace_id_py: Option<Py<PyAny>>,
    /// Storage for meta_struct values: dict[str, Any].
    /// None until first use; initialized to an empty dict in __new__.
    pub meta_struct: Option<Py<PyDict>>,
}

impl SpanData {
    /// Set `data.trace_id` and invalidate `_trace_id_py`.
    ///
    /// **All writes to `data.trace_id` must go through this method** to keep `_trace_id_py`
    /// consistent. Bypassing it leaves a stale cached Python int that silently returns the
    /// old value on the next `span.trace_id` read.
    #[inline(always)]
    pub fn set_trace_id_native(&mut self, id: u128) {
        self.data.trace_id = id;
        self._trace_id_py = None;
    }

    /// Setdefault helper for `_set_default_attributes`: insert one key/value pair only if
    /// the key is not already present in either meta or metrics.
    fn set_default_attribute_entry(&mut self, k: &Bound<'_, PyAny>, v: &Bound<'_, PyAny>) {
        if !self.has_attribute(k) {
            let _ = self.set_attribute(k, v);
        }
    }
}

const HTTP_STATUS_CODE_KEY: &str = "http.status_code";

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

    // ── Attribute API (meta / metrics) ──────────────────────────────────────

    /// Set a tag/metric on the span. Stores the value in the unified `attributes` map,
    /// preserving the original Python type (str → Str, int/bool → Int, float → Float).
    ///
    /// Special case: `http.status_code` is always coerced to a string so the trace agent
    /// can compute HTTP metrics from the meta tag.
    ///
    /// Supported value types: str, int, float. Other types are coerced on a best-effort
    /// basis (bytes → UTF-8 decoded str, oversized ints → str, arbitrary objects → str).
    #[pyo3(name = "_set_attribute")]
    fn set_attribute(
        &mut self,
        key: &Bound<'_, PyAny>,
        value: &Bound<'_, PyAny>,
    ) -> pyo3::PyResult<()> {
        let Ok(key_str) = key.cast::<PyString>() else {
            return Ok(());
        };
        let attr_key = AttrKey::new(key_str.clone().unbind());

        // http.status_code must always be a string in meta.
        // Fast path: typed contract is `str`, so most callers already pass a PyString.
        // Only fall back to str() for non-string inputs (e.g. an int 200).
        if key_str.to_str().unwrap_or("") == HTTP_STATUS_CODE_KEY {
            let s = if let Ok(s) = value.cast::<PyString>() {
                s.clone()
            } else {
                let Ok(s) = value.str() else {
                    return Ok(());
                };
                s
            };
            self.attributes.insert(attr_key, AttributeValue::Str(s.unbind()));
            return Ok(());
        }

        // str → Str
        if let Ok(s) = value.cast::<PyString>() {
            self.attributes.insert(attr_key, AttributeValue::Str(s.clone().unbind()));
            return Ok(());
        }

        // float → Float (drop NaN/Inf)
        // Check before int because some types (e.g. numpy.float64) implement __float__
        // but not __index__, so PyFloat succeeds and PyInt would fail.
        if let Ok(f) = value.cast::<PyFloat>() {
            let n = f.value();
            if n.is_nan() || n.is_infinite() {
                return Ok(());
            }
            self.attributes.insert(attr_key, AttributeValue::Float(n));
            return Ok(());
        }

        // int (catches bool and numpy.int* via __index__) → Int.
        // extract::<i64>() succeeds for bool (True → 1, False → 0) and for any
        // type implementing __index__. Python ints that overflow i64 fall through
        // to the str() fallback below.
        if let Ok(n) = value.extract::<i64>() {
            self.attributes.insert(attr_key, AttributeValue::Int(n));
            return Ok(());
        }

        // bytes → UTF-8 decoded Str (with U+FFFD replacements for invalid sequences)
        if let Ok(b) = value.cast::<PyBytes>() {
            let decoded = String::from_utf8_lossy(b.as_bytes());
            let py_str = PyString::new(key.py(), &decoded);
            self.attributes.insert(attr_key, AttributeValue::Str(py_str.unbind()));
            return Ok(());
        }

        // Fallback: str(value) — covers Python ints that overflow i64, arbitrary objects, etc.
        let Ok(s) = value.str() else {
            return Ok(());
        };
        self.attributes.insert(attr_key, AttributeValue::Str(s.unbind()));
        Ok(())
    }

    /// Set multiple attributes from a dict/mapping, routing each value via `_set_attribute`.
    ///
    /// Accepts any Python dict (fast path) or any object that implements the mapping protocol
    /// (e.g. `collections.OrderedDict`, `types.MappingProxyType`). If the argument supports
    /// neither, the call is a no-op. Invalid value types follow the same coercion rules as
    /// `_set_attribute`.
    #[pyo3(name = "_set_attributes")]
    fn set_attributes(&mut self, attrs: &Bound<'_, PyAny>) -> pyo3::PyResult<()> {
        if let Ok(d) = attrs.cast_exact::<PyDict>() {
            for (k, v) in d.iter() {
                let _ = self.set_attribute(&k, &v);
            }
        } else if let Ok(m) = attrs.cast::<PyMapping>() {
            if let Ok(items) = m.items() {
                for item in items.iter() {
                    let Ok(pair) = item.cast::<PyTuple>() else {
                        continue;
                    };
                    let Ok(k) = pair.get_item(0) else {
                        continue;
                    };
                    let Ok(v) = pair.get_item(1) else {
                        continue;
                    };
                    let _ = self.set_attribute(&k, &v);
                }
            }
        }
        // Not a dict or mapping — bail silently.
        Ok(())
    }

    /// Return True if the span has an attribute with the given key.
    #[pyo3(name = "_has_attribute")]
    fn has_attribute(&self, key: &Bound<'_, PyAny>) -> bool {
        let Ok(k) = key.cast::<PyString>() else {
            return false;
        };
        let Ok(k_str) = k.to_str() else {
            return false;
        };
        self.attributes.contains_key(k_str)
    }

    /// Remove an attribute by key.
    #[pyo3(name = "_remove_attribute")]
    fn remove_attribute(&mut self, key: &Bound<'_, PyAny>) {
        let Ok(k) = key.cast::<PyString>() else {
            return;
        };
        let Ok(k_str) = k.to_str() else {
            return;
        };
        self.attributes.remove(k_str);
    }

    /// Return the raw stored value for the given key, or None if not found.
    /// Returns the natural Python type: str for Str, int for Int, float for Float.
    #[pyo3(name = "_get_attribute")]
    fn get_attribute<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let k = key.cast::<PyString>().ok()?;
        let k_str = k.to_str().ok()?;
        Some(self.attributes.get(k_str)?.as_py(py))
    }

    /// Return the string attribute for the given key, or None if not a Str variant.
    #[pyo3(name = "_get_str_attribute")]
    fn get_str_attribute<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let k = key.cast::<PyString>().ok()?;
        let k_str = k.to_str().ok()?;
        match self.attributes.get(k_str)? {
            AttributeValue::Str(s) => Some(s.bind(py).clone().into_any()),
            _ => None,
        }
    }

    /// Return the numeric attribute for the given key, or None if not a numeric variant.
    /// Returns int for Int values and float for Float values, preserving the original type.
    #[pyo3(name = "_get_numeric_attribute")]
    fn get_numeric_attribute<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let k = key.cast::<PyString>().ok()?;
        let k_str = k.to_str().ok()?;
        match self.attributes.get(k_str)? {
            AttributeValue::Int(i) => {
                Some(i.into_pyobject(py).expect("i64 into_pyobject").into_any())
            }
            AttributeValue::Float(f) => {
                Some(f.into_pyobject(py).expect("f64 into_pyobject").into_any())
            }
            AttributeValue::Str(_) => None,
        }
    }

    /// Return all attributes merged into a single dict.
    /// Values are the natural Python type (str, int, or float).
    /// Used by callers that propagate span attributes (e.g. parent-span copy).
    #[pyo3(name = "_get_attributes")]
    fn get_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.attributes {
            d.set_item(k.as_bound(py), v.as_py(py))?;
        }
        Ok(d)
    }

    /// Return all Str-variant attributes as a Python dict snapshot.
    /// Used by the Python encoder to build the v0.4 `meta` dict.
    /// Note: Int values with abs > 2^53 are NOT folded in here; the encoder
    /// is responsible for moving them from metrics to meta at encode time.
    #[pyo3(name = "_get_str_attributes")]
    fn get_str_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.attributes {
            if let AttributeValue::Str(s) = v {
                d.set_item(k.as_bound(py), s.bind(py))?;
            }
        }
        Ok(d)
    }

    /// Return all numeric (Int and Float) attributes as a Python dict snapshot.
    /// Int values are returned as Python int; Float values as Python float.
    /// Used by the Python encoder to build the v0.4 `metrics` dict.
    /// Note: the encoder is responsible for moving Int values with abs > 2^53
    /// out of metrics and into meta as strings before serialization.
    #[pyo3(name = "_get_numeric_attributes")]
    fn get_numeric_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.attributes {
            match v {
                AttributeValue::Int(i) => {
                    d.set_item(k.as_bound(py), *i)?;
                }
                AttributeValue::Float(f) => {
                    d.set_item(k.as_bound(py), *f)?;
                }
                AttributeValue::Str(_) => {}
            }
        }
        Ok(d)
    }

    /// Apply setdefault semantics from a Python dict/mapping: for each key/value pair,
    /// if the key is not already present in either meta or metrics, insert it
    /// (routing str→meta, numeric→metrics). Keys that already exist are skipped.
    ///
    /// Accepts any Python dict (fast path) or mapping. Bails silently on bad input.
    /// Used by callers that previously called `_update_tags_from_context`.
    /// Callers handle any locking on the source dict themselves.
    #[pyo3(name = "_set_default_attributes")]
    fn set_default_attributes(&mut self, values: &Bound<'_, PyAny>) -> pyo3::PyResult<()> {
        if let Ok(d) = values.cast_exact::<PyDict>() {
            for (k, v) in d.iter() {
                self.set_default_attribute_entry(&k, &v);
            }
        } else if let Ok(m) = values.cast::<PyMapping>() {
            if let Ok(items) = m.items() {
                for item in items.iter() {
                    let Ok(pair) = item.cast::<PyTuple>() else {
                        continue;
                    };
                    let Ok(k) = pair.get_item(0) else {
                        continue;
                    };
                    let Ok(v) = pair.get_item(1) else {
                        continue;
                    };
                    self.set_default_attribute_entry(&k, &v);
                }
            }
        }
        // Not a dict or mapping — bail silently.
        Ok(())
    }
    // meta_struct methods

    fn _set_struct_tag(
        &mut self,
        py: Python<'_>,
        key: &str,
        value: &Bound<'_, PyDict>,
    ) -> pyo3::PyResult<()> {
        let dict = self
            .meta_struct
            .get_or_insert_with(|| PyDict::new(py).unbind())
            .bind(py);
        dict.set_item(key, value)
    }

    fn _get_struct_tag<'py>(
        &self,
        py: Python<'py>,
        key: &str,
    ) -> pyo3::PyResult<Option<Bound<'py, PyAny>>> {
        match &self.meta_struct {
            None => Ok(None),
            Some(dict) => dict.bind(py).get_item(key),
        }
    }

    fn _remove_struct_tag<'py>(
        &mut self,
        py: Python<'py>,
        key: &str,
    ) -> pyo3::PyResult<Option<Bound<'py, PyAny>>> {
        match &self.meta_struct {
            None => Ok(None),
            Some(dict) => {
                let dict = dict.bind(py);
                let value = dict.get_item(key)?;
                if value.is_some() {
                    dict.del_item(key)?;
                }
                Ok(value)
            }
        }
    }

    fn _has_meta_structs(&self, py: Python<'_>) -> bool {
        self.meta_struct
            .as_ref()
            .map(|d| !d.bind(py).is_empty())
            .unwrap_or(false)
    }

    fn _get_meta_structs<'py>(&self, py: Python<'py>) -> Bound<'py, PyDict> {
        match &self.meta_struct {
            None => PyDict::new(py),
            Some(dict) => dict.bind(py).clone(),
        }
    }
    // --- Span links ---

    /// Add a span link to native storage from raw fields (avoids constructing a PyO3 SpanLink).
    /// Applies dedup logic: span pointers are always appended;
    /// regular links replace any existing link with the same span_id.
    #[pyo3(signature = (trace_id, span_id, tracestate=None, flags=None, attributes=None))]
    fn _set_link(
        &mut self,
        py: Python<'_>,
        trace_id: u128,
        span_id: u64,
        tracestate: Option<&Bound<'_, PyAny>>,
        flags: Option<i64>,
        attributes: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<()> {
        let attrs = match attributes {
            None => Default::default(),
            Some(obj) if obj.is_none() => Default::default(),
            Some(obj) => {
                if let Ok(dict) = obj.cast_exact::<PyDict>() {
                    py_dict_to_link_attrs(py, dict)?
                } else {
                    // Accept any mapping (e.g. OTel BoundedAttributes)
                    let dict = PyDict::new(py);
                    let mapping = obj.cast::<PyMapping>()?;
                    dict.update(mapping)?;
                    py_dict_to_link_attrs(py, &dict)?
                }
            }
        };

        // DEV: is_span_pointer must be computed before build_native_link, which consumes attrs by value.
        let is_span_pointer = attrs
            .get(&PyBackedString::from_static_str("link.kind"))
            .is_some_and(|v| v.as_ref() as &str == "span-pointer");

        // Extract tracestate as PyBackedString; silently default to empty for None or non-string values.
        let tracestate = tracestate
            .and_then(|obj| obj.extract::<PyBackedString>().ok())
            .filter(|s| !s.is_empty())
            .unwrap_or_default();

        let native_link = build_native_link(trace_id, span_id, tracestate, flags, attrs);

        if is_span_pointer {
            self.data.span_links.push(native_link);
        } else {
            match self
                .data
                .span_links
                .iter()
                .position(|l| l.span_id == span_id)
            {
                Some(idx) => self.data.span_links[idx] = native_link,
                None => self.data.span_links.push(native_link),
            }
        }
        Ok(())
    }

    /// Add a SpanEvent to native storage.
    #[pyo3(signature = (name, attributes = None, time_unix_nano = None))]
    fn _add_event(
        &mut self,
        py: Python<'_>,
        name: &Bound<'_, PyAny>,
        attributes: Option<&Bound<'_, PyAny>>,
        time_unix_nano: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<()> {
        let name = extract_backed_string_or_default(name);
        let time_unix_nano = extract_time_unix_nano(time_unix_nano);
        let attrs = match attributes {
            None => Default::default(),
            Some(obj) if obj.is_none() => Default::default(),
            Some(obj) => {
                if let Ok(dict) = obj.cast_exact::<PyDict>() {
                    py_dict_to_event_attrs(py, dict)?
                } else {
                    // Accept any mapping
                    let dict = PyDict::new(py);
                    let mapping = obj.cast::<PyMapping>()?;
                    dict.update(mapping)?;
                    py_dict_to_event_attrs(py, &dict)?
                }
            }
        };
        self.data.span_events.push(NativeSpanEvent {
            name,
            time_unix_nano,
            attributes: attrs,
        });
        Ok(())
    }

    /// Materialize all stored links back to PyO3 SpanLink objects.
    fn _get_links(&self, py: Python<'_>) -> PyResult<Vec<Py<SpanLink>>> {
        self.data
            .span_links
            .iter()
            .map(|l| native_span_link_to_py(py, l))
            .collect()
    }

    /// Materialize all stored events back to PyO3 SpanEvent objects.
    fn _get_events(&self, py: Python<'_>) -> PyResult<Vec<Py<SpanEvent>>> {
        self.data
            .span_events
            .iter()
            .map(|e| native_span_event_to_py(py, e))
            .collect()
    }

    fn _has_links(&self) -> bool {
        !self.data.span_links.is_empty()
    }

    fn _has_events(&self) -> bool {
        !self.data.span_events.is_empty()
    }
}

// --- Conversion helpers ---

/// Build a native SpanLink from raw fields and a pre-computed attributes HashMap.
/// `tracestate` should already be extracted as a PyBackedString (empty for absent).
fn build_native_link(
    trace_id: u128,
    span_id: u64,
    tracestate: PyBackedString,
    flags: Option<i64>,
    attrs: std::collections::HashMap<PyBackedString, PyBackedString>,
) -> NativeSpanLink<PyTraceData> {
    let trace_id_low = trace_id as u64;
    let trace_id_high = (trace_id >> 64) as u64;
    // Encode "flags present" using bit 31: None -> 0, Some(f) -> f as u32 | 0x8000_0000.
    let flags = match flags {
        None => 0u32,
        Some(f) => (f as u32) | 0x8000_0000u32,
    };
    NativeSpanLink {
        trace_id: trace_id_low,
        trace_id_high,
        span_id,
        attributes: attrs,
        tracestate,
        flags,
    }
}

/// Convert attributes from a PyDict to a HashMap<PyBackedString, PyBackedString> for SpanLink storage.
/// Flattens nested sequences and stringifies all values (mirrors SpanLink::to_dict() logic).
fn py_dict_to_link_attrs(
    py: Python<'_>,
    dict: &Bound<'_, PyDict>,
) -> PyResult<std::collections::HashMap<PyBackedString, PyBackedString>> {
    let mut out = std::collections::HashMap::new();
    for (k, v) in dict.iter() {
        for (fk, fv) in flatten_key_value_vec_fn(py, &k, &v)? {
            // fk is a Bound<PyAny> (Python string) — extract as PyBackedString (zero-copy borrow)
            let key: PyBackedString = fk.extract()?;
            // Stringify value: bools as lowercase, others via Python str().
            let py_val_str = if fv.is_instance_of::<PyBool>() {
                let b: bool = fv.extract()?;
                if b { "true" } else { "false" }
                    .into_pyobject(py)?
                    .into_any()
            } else {
                fv.str()?.into_any()
            };
            let val: PyBackedString = py_val_str.extract()?;
            out.insert(key, val);
        }
    }
    Ok(out)
}

/// Convert a PyDict to a HashMap<PyBackedString, AttributeAnyValue<PyTraceData>> for SpanEvent storage.
fn py_dict_to_event_attrs(
    py: Python<'_>,
    dict: &Bound<'_, PyDict>,
) -> PyResult<std::collections::HashMap<PyBackedString, AttributeAnyValue<PyTraceData>>> {
    let mut out = std::collections::HashMap::new();
    for (k, v) in dict.iter() {
        let key: PyBackedString = k.extract()?;
        let val = py_value_to_attribute_any_value(py, &v)?;
        out.insert(key, val);
    }
    Ok(out)
}

/// Convert a Python value to AttributeAnyValue<PyTraceData>.
fn py_value_to_attribute_any_value(
    py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> PyResult<AttributeAnyValue<PyTraceData>> {
    // Must check bool before int (bool is a subclass of int in Python)
    if obj.is_instance_of::<PyBool>() {
        let b: bool = obj.extract()?;
        return Ok(AttributeAnyValue::SingleValue(
            AttributeArrayValue::Boolean(b),
        ));
    }
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(AttributeAnyValue::SingleValue(
            AttributeArrayValue::Integer(i),
        ));
    }
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(AttributeAnyValue::SingleValue(AttributeArrayValue::Double(
            f,
        )));
    }
    if obj.is_instance_of::<PyList>() {
        let list = obj.cast::<PyList>()?;
        let mut items = Vec::with_capacity(list.len());
        for item in list.iter() {
            items.push(py_value_to_array_value(py, &item)?);
        }
        return Ok(AttributeAnyValue::Array(items));
    }
    // Default: stringify as string
    let s: PyBackedString = obj
        .extract::<PyBackedString>()
        .or_else(|_| obj.str()?.extract::<PyBackedString>())?;
    Ok(AttributeAnyValue::SingleValue(AttributeArrayValue::String(
        s,
    )))
}

/// Convert a Python value to AttributeArrayValue<PyTraceData> (for array elements).
fn py_value_to_array_value(
    _py: Python<'_>,
    obj: &Bound<'_, PyAny>,
) -> PyResult<AttributeArrayValue<PyTraceData>> {
    if obj.is_instance_of::<PyBool>() {
        let b: bool = obj.extract()?;
        return Ok(AttributeArrayValue::Boolean(b));
    }
    if let Ok(i) = obj.extract::<i64>() {
        return Ok(AttributeArrayValue::Integer(i));
    }
    if let Ok(f) = obj.extract::<f64>() {
        return Ok(AttributeArrayValue::Double(f));
    }
    let s: PyBackedString = obj
        .extract()
        .or_else(|_| obj.str()?.extract::<PyBackedString>())?;
    Ok(AttributeArrayValue::String(s))
}

/// Materialize a native SpanLink<PyTraceData> back to a PyO3 SpanLink.
fn native_span_link_to_py(
    py: Python<'_>,
    link: &NativeSpanLink<PyTraceData>,
) -> PyResult<Py<SpanLink>> {
    let trace_id = (link.trace_id as u128) | ((link.trace_id_high as u128) << 64);
    let tracestate = if link.tracestate.is_empty() {
        None
    } else {
        Some(link.tracestate.clone_ref(py))
    };
    // Bit 31 of native flags encodes "flags present": 0 means None, otherwise strip bit 31.
    let flags = if link.flags & 0x8000_0000 != 0 {
        Some((link.flags & 0x7FFF_FFFF) as i64)
    } else {
        None
    };
    // Reconstruct attributes dict from flat string->string map.
    // &PyBackedString implements IntoPyObject — for Python-backed strings this is a zero-copy
    // incref of the original Python object; for static strings it creates an interned PyString.
    let attrs = PyDict::new(py);
    for (k, v) in link.attributes.iter() {
        attrs.set_item(k, v)?;
    }
    Py::new(
        py,
        SpanLink {
            trace_id,
            span_id: link.span_id,
            tracestate,
            flags,
            attributes: attrs.unbind(),
        },
    )
}

/// Materialize a native SpanEvent<PyTraceData> back to a PyO3 SpanEvent.
fn native_span_event_to_py(
    py: Python<'_>,
    event: &NativeSpanEvent<PyTraceData>,
) -> PyResult<Py<SpanEvent>> {
    let attrs = PyDict::new(py);
    for (k, v) in &event.attributes {
        let py_val = attribute_any_value_to_py(py, v)?;
        attrs.set_item(k, py_val)?;
    }
    Py::new(
        py,
        SpanEvent {
            name: event.name.clone_ref(py),
            time_unix_nano: event.time_unix_nano,
            attributes: attrs.unbind(),
        },
    )
}

/// Convert an AttributeAnyValue<PyTraceData> back to a Python object.
fn attribute_any_value_to_py(
    py: Python<'_>,
    val: &AttributeAnyValue<PyTraceData>,
) -> PyResult<Py<PyAny>> {
    match val {
        AttributeAnyValue::SingleValue(v) => array_value_to_py(py, v),
        AttributeAnyValue::Array(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(array_value_to_py(py, item)?)?;
            }
            Ok(list.into_any().unbind())
        }
    }
}

/// Convert an AttributeArrayValue<PyTraceData> back to a Python object.
fn array_value_to_py(
    py: Python<'_>,
    val: &AttributeArrayValue<PyTraceData>,
) -> PyResult<Py<PyAny>> {
    match val {
        AttributeArrayValue::String(s) => Ok(s.as_py(py).unbind()),
        AttributeArrayValue::Boolean(b) => Ok(PyBool::new(py, *b).to_owned().into_any().unbind()),
        AttributeArrayValue::Integer(i) => Ok(i.into_pyobject(py)?.into_any().unbind()),
        AttributeArrayValue::Double(f) => Ok(f.into_pyobject(py)?.into_any().unbind()),
    }
}
