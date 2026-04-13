use pyo3::{
    types::{
        PyAnyMethods as _, PyBytes, PyBytesMethods as _, PyDict, PyDictMethods as _, PyFloat,
        PyInt, PyListMethods as _, PyMapping, PyMappingMethods as _, PyString, PyTuple,
    },
    Bound, IntoPyObject as _, Py, PyAny, Python,
};

use crate::py_string::{PyBackedString, PyTraceData};
use libdd_trace_utils::span::SpanText as _;

use super::utils::{
    extract_backed_string_or_default, extract_backed_string_or_none, extract_i32_or_default,
    extract_i64_or_default, try_extract_backed_string, wall_clock_ns,
};

#[pyo3::pyclass(name = "SpanData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct SpanData {
    pub data: libdd_trace_utils::span::v04::Span<PyTraceData>,
    pub span_api: PyBackedString,
    /// Lazy Python int cache for the `trace_id` getter.
    /// Populated on first read; invalidated on every write to `data.trace_id`.
    /// `data.trace_id` is always the source of truth.
    pub _trace_id_py: Option<Py<PyAny>>,
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
}

/// `http.status_code` must always be stored in meta (not metrics) because
/// the trace agent calculates HTTP metrics from it.
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

    /// Set a tag/metric on the span. Routes string values to meta, numeric values to
    /// metrics, deleting the key from the other map to avoid duplicates.
    ///
    /// Special case: `http.status_code` is always stored as a string in meta,
    /// regardless of the value type.
    ///
    /// Supported value types: str, int, float, bool (stored as 0/1 metric), bytes
    /// (decoded as UTF-8 string). Any other type is coerced via str(value).
    #[pyo3(name = "_set_attribute")]
    fn set_attribute(
        &mut self,
        key: &Bound<'_, PyAny>,
        value: &Bound<'_, PyAny>,
    ) -> pyo3::PyResult<()> {
        let Some(key_pbs) = try_extract_backed_string(key) else {
            return Ok(());
        };

        if let Ok(s) = value.cast::<PyString>() {
            let Some(v) = PyBackedString::try_from(s.clone()).ok() else {
                return Ok(());
            };
            self.data.metrics.remove(&*key_pbs);
            self.data.meta.insert(key_pbs, v);
        } else if &*key_pbs == HTTP_STATUS_CODE_KEY {
            // Force http.status_code to string regardless of value type
            let Ok(s) = value.str() else {
                return Ok(());
            };
            let Some(v) = PyBackedString::try_from(s.clone()).ok() else {
                return Ok(());
            };
            self.data.metrics.remove(&*key_pbs);
            self.data.meta.insert(key_pbs, v);
        } else if value.cast::<PyFloat>().is_ok() {
            let Ok(n) = value.extract::<f64>() else {
                return Ok(());
            };
            if n.is_nan() || n.is_infinite() {
                return Ok(());
            }
            self.data.meta.remove(&*key_pbs);
            self.data.metrics.insert(key_pbs, n);
        } else if value.cast::<PyInt>().is_ok() {
            // Catches int and bool (PyBool subclasses PyInt); downcast (not downcast_exact)
            // is used so subclasses are included.
            let Ok(n) = value.extract::<f64>() else {
                return Ok(());
            };
            self.data.meta.remove(&*key_pbs);
            self.data.metrics.insert(key_pbs, n);
        } else if let Ok(b) = value.cast::<PyBytes>() {
            // Decode bytes as UTF-8 (with U+FFFD replacements for invalid sequences).
            let decoded = String::from_utf8_lossy(b.as_bytes());
            let py = key.py();
            let py_str = PyString::new(py, &decoded);
            let Some(v) = PyBackedString::try_from(py_str).ok() else {
                return Ok(());
            };
            self.data.metrics.remove(&*key_pbs);
            self.data.meta.insert(key_pbs, v);
        } else {
            // Fallback: coerce to string via str(value). Swallow any Python exception.
            let Ok(s) = value.str() else {
                return Ok(());
            };
            let Some(v) = PyBackedString::try_from(s.clone()).ok() else {
                return Ok(());
            };
            self.data.metrics.remove(&*key_pbs);
            self.data.meta.insert(key_pbs, v);
        }
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
        if let Ok(d) = attrs.cast::<PyDict>() {
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

    /// Return True if the span has an attribute (string or numeric) with the given key.
    #[pyo3(name = "_has_attribute")]
    fn has_attribute(&self, key: &Bound<'_, PyAny>) -> bool {
        let Some(k) = try_extract_backed_string(key) else {
            return false;
        };
        self.data.meta.contains_key(&*k) || self.data.metrics.contains_key(&*k)
    }

    /// Remove an attribute (from both meta and metrics) by key.
    #[pyo3(name = "_remove_attribute")]
    fn remove_attribute(&mut self, key: &Bound<'_, PyAny>) {
        let Some(k) = try_extract_backed_string(key) else {
            return;
        };
        self.data.meta.remove(&*k);
        self.data.metrics.remove(&*k);
    }

    /// Return the attribute value for the given key, or None if not found.
    /// Meta (string) attributes take priority over metrics with the same key.
    #[pyo3(name = "_get_attribute")]
    fn get_attribute<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let k = try_extract_backed_string(key)?;
        if let Some(v) = self.data.meta.get(&*k) {
            Some(v.as_py(py))
        } else if let Some(&n) = self.data.metrics.get(&*k) {
            Some(n.into_pyobject(py).expect("f64 into_pyobject").into_any())
        } else {
            None
        }
    }

    /// Return the string attribute for the given key, or None if not found.
    #[pyo3(name = "_get_str_attribute")]
    fn get_str_attribute<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let k = try_extract_backed_string(key)?;
        self.data.meta.get(&*k).map(|v| v.as_py(py))
    }

    /// Return the numeric attribute for the given key, or None if not found.
    #[pyo3(name = "_get_numeric_attribute")]
    fn get_numeric_attribute(&self, key: &Bound<'_, PyAny>) -> Option<f64> {
        let k = try_extract_backed_string(key)?;
        self.data.metrics.get(&*k).copied()
    }

    /// Return all attributes (both string and numeric) merged into a single dict.
    /// Numeric attributes override string attributes for the same key (matches Python
    /// `{**self._meta, **self._metrics}` semantics).
    #[pyo3(name = "_get_attributes")]
    fn get_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.data.meta {
            d.set_item(k.as_py(py), v.as_py(py))?;
        }
        for (k, &v) in &self.data.metrics {
            d.set_item(k.as_py(py), v)?;
        }
        Ok(d)
    }

    /// Return all string attributes as a Python dict snapshot.
    #[pyo3(name = "_get_str_attributes")]
    fn get_str_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.data.meta {
            d.set_item(k.as_py(py), v.as_py(py))?;
        }
        Ok(d)
    }

    /// Return all numeric attributes as a Python dict snapshot.
    #[pyo3(name = "_get_numeric_attributes")]
    fn get_numeric_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, &v) in &self.data.metrics {
            d.set_item(k.as_py(py), v)?;
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
        if let Ok(d) = values.cast::<PyDict>() {
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
}

impl SpanData {
    /// Inner logic for `_set_default_attributes`: insert one key/value pair with setdefault
    /// semantics. Bails silently on bad key type, bad value type, or duplicate key.
    fn set_default_attribute_entry(&mut self, k: &Bound<'_, PyAny>, v: &Bound<'_, PyAny>) {
        let Some(key_pbs) = try_extract_backed_string(k) else {
            return;
        };

        // Setdefault: skip keys already present in either map
        if self.data.meta.contains_key(&*key_pbs) || self.data.metrics.contains_key(&*key_pbs) {
            return;
        }

        if let Ok(s) = v.cast::<PyString>() {
            let Some(val) = PyBackedString::try_from(s.clone()).ok() else {
                return;
            };
            self.data.meta.insert(key_pbs, val);
        } else if v.cast::<PyFloat>().is_ok() || v.cast::<PyInt>().is_ok() {
            let Ok(n) = v.extract::<f64>() else {
                return;
            };
            if n.is_nan() || n.is_infinite() {
                return;
            }
            self.data.metrics.insert(key_pbs, n);
        }
        // Skip other types — context dicts only contain str and numeric values
    }
}
