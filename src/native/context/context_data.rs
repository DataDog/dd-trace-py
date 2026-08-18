use std::sync::OnceLock;

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use pyo3::{
    exceptions::PyValueError,
    types::{PyAny, PyAnyMethods as _, PyDict, PyDictMethods as _, PyList, PyTuple},
    Bound, IntoPyObject, Py, PyResult, Python,
};

const ORIGIN_KEY: &str = "_dd.origin";
const SAMPLING_PRIORITY_KEY: &str = "_sampling_priority_v1";
const USER_ID_KEY: &str = "_dd.p.usr.id";
const W3C_TRACEPARENT_KEY: &str = "traceparent";
const W3C_TRACESTATE_KEY: &str = "tracestate";
const MAX_UINT_64BITS: u128 = (1u128 << 64) - 1;

type ContextState<'py> = (
    Option<u128>,
    Option<u128>,
    Bound<'py, PyDict>,
    Bound<'py, PyDict>,
    Bound<'py, PyList>,
    Bound<'py, PyDict>,
    bool,
    bool,
);

// Cached lookups of Python callables this module needs repeatedly. Both are
// resolved lazily (on first use, not at module-import time) to avoid forcing
// an import order on `ddtrace.internal.threads` / `ddtrace.internal.utils.http`.
static RLOCK_CLASS: OnceLock<Py<PyAny>> = OnceLock::new();
static W3C_GET_DD_LIST_MEMBER: OnceLock<Py<PyAny>> = OnceLock::new();

fn rlock_class(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    if let Some(v) = RLOCK_CLASS.get() {
        return Ok(v.bind(py).clone());
    }
    // Imported from `ddtrace.internal.threads` (not `_thread`/`threading` directly) to
    // reuse its pre-monkeypatch reference to the original lock class -- see that
    // module's docstring for why.
    let cls = py.import("ddtrace.internal.threads")?.getattr("RLock")?;
    let _ = RLOCK_CLASS.set(cls.clone().unbind());
    Ok(cls)
}

fn w3c_get_dd_list_member_fn(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    if let Some(v) = W3C_GET_DD_LIST_MEMBER.get() {
        return Ok(v.bind(py).clone());
    }
    let f = py
        .import("ddtrace.internal.utils.http")?
        .getattr("w3c_get_dd_list_member")?;
    let _ = W3C_GET_DD_LIST_MEMBER.set(f.clone().unbind());
    Ok(f)
}

/// Matches Python's `re.compile(r"[^\x20-\x7E]+").search(s) is None`.
fn is_printable_ascii(s: &str) -> bool {
    s.chars().all(|c| (0x20..=0x7E).contains(&(c as u32)))
}

fn del_item_if_present(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<()> {
    if dict.contains(key)? {
        dict.del_item(key)?;
    }
    Ok(())
}

/// Extracts a trace id from a Python int (only). Mirrors `set_trace_id`.
fn extract_trace_id(v: Option<&Bound<'_, PyAny>>) -> Option<u128> {
    v.and_then(|v| v.extract::<u128>().ok())
}

/// Extracts a span id from a Python int or a base-10 string. Mirrors `set_span_id`.
fn extract_span_id(v: Option<&Bound<'_, PyAny>>) -> Option<u128> {
    let v = v?;
    if let Ok(id) = v.extract::<u128>() {
        return Some(id);
    }
    v.extract::<&str>()
        .ok()
        .and_then(|s| s.parse::<u128>().ok())
}

/// RAII guard around a Python lock object's `acquire()`/`release()`, mirroring
/// `with self._lock:`. Always releases on drop, including on early return via `?`.
struct LockGuard<'py> {
    lock: Bound<'py, PyAny>,
}

impl<'py> LockGuard<'py> {
    fn acquire(lock: Bound<'py, PyAny>) -> PyResult<Self> {
        lock.call_method0("acquire")?;
        Ok(Self { lock })
    }
}

impl Drop for LockGuard<'_> {
    fn drop(&mut self) {
        // Best-effort: nothing sane to do if `release()` itself raises (e.g. during
        // interpreter teardown), and `Drop` cannot propagate errors.
        let _ = self.lock.call_method0("release");
    }
}

/// Native storage layer AND business logic for `ddtrace._trace.context.Context`.
///
/// Mirrors the `SpanData`/`Span` split in structure, but unlike `SpanData` this
/// class owns essentially all of `Context`'s behavior (id/meta/metrics/baggage
/// storage, the trace-level lock, W3C traceparent/tracestate computation,
/// sampling_priority/dd_origin/dd_user_id, baggage helpers, copy(), equality,
/// pickling) -- `ddtrace._trace.context.Context` is a near-empty subclass kept
/// only so user-facing code keeps importing a stable Python name.
#[pyo3::pyclass(name = "ContextData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct ContextData {
    pub trace_id: Option<u128>,
    pub span_id: Option<u128>,
    /// dict[str, str]. `None` only in the brief window before `__new__` finishes
    /// or after `__clear__` runs during GC teardown -- getters lazily
    /// re-materialize an empty dict, mirroring `SpanData::meta_struct`.
    meta: Option<Py<PyDict>>,
    /// dict[str, NumericType].
    metrics: Option<Py<PyDict>>,
    /// dict[str, Any].
    baggage: Option<Py<PyDict>>,
    /// list[SpanLink].
    span_links: Option<Py<PyList>>,
    /// A `_thread.RLock`-like object (see `ddtrace.internal.threads.RLock`), shared
    /// by identity across `copy()`'d contexts belonging to the same trace.
    lock: Option<Py<PyAny>>,
    #[pyo3(get, set, name = "_is_remote")]
    pub is_remote: bool,
    #[pyo3(get, set, name = "_reactivate")]
    pub reactivate: bool,
}

impl ContextData {
    /// Lazily materializes and returns the trace-level lock, mirroring the
    /// `_meta`/`_metrics`/etc. lazy-init pattern.
    fn lock_bound<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        if self.lock.is_none() {
            self.lock = Some(rlock_class(py)?.call0()?.unbind());
        }
        Ok(self.lock.as_ref().unwrap().bind(py).clone())
    }

    fn sampling_priority_f64(&mut self, py: Python<'_>) -> PyResult<Option<f64>> {
        let metrics = self.get_metrics(py);
        match metrics.get_item(SAMPLING_PRIORITY_KEY)? {
            Some(v) => Ok(Some(v.extract::<f64>()?)),
            None => Ok(None),
        }
    }
}

#[pyo3::pymethods]
impl ContextData {
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        trace_id=None,
        span_id=None,
        dd_origin=None,
        sampling_priority=None,
        meta=None,
        metrics=None,
        lock=None,
        span_links=None,
        baggage=None,
        is_remote=true,
        *args,
        **kwargs
    ))]
    pub fn __new__<'p>(
        py: Python<'p>,
        trace_id: Option<&Bound<'p, PyAny>>,
        span_id: Option<&Bound<'p, PyAny>>,
        dd_origin: Option<&Bound<'p, PyAny>>,
        sampling_priority: Option<&Bound<'p, PyAny>>,
        meta: Option<&Bound<'p, PyDict>>,
        metrics: Option<&Bound<'p, PyDict>>,
        lock: Option<&Bound<'p, PyAny>>,
        span_links: Option<&Bound<'p, PyList>>,
        baggage: Option<&Bound<'p, PyDict>>,
        is_remote: bool,
        // Accept *args/**kwargs so subclasses don't need to override __new__
        args: &Bound<'p, PyTuple>,
        kwargs: Option<&Bound<'p, PyDict>>,
    ) -> PyResult<Self> {
        let _ = (args, kwargs);
        let meta_dict = meta
            .map(|d| d.clone().unbind())
            .unwrap_or_else(|| PyDict::new(py).unbind());
        if let Some(origin) = dd_origin {
            if let Ok(s) = origin.extract::<&str>() {
                if is_printable_ascii(s) {
                    meta_dict.bind(py).set_item(ORIGIN_KEY, s)?;
                }
            }
        }
        let metrics_dict = metrics
            .map(|d| d.clone().unbind())
            .unwrap_or_else(|| PyDict::new(py).unbind());
        if let Some(sp) = sampling_priority {
            metrics_dict.bind(py).set_item(SAMPLING_PRIORITY_KEY, sp)?;
        }
        let lock_obj = match lock {
            Some(l) => l.clone().unbind(),
            None => rlock_class(py)?.call0()?.unbind(),
        };
        Ok(Self {
            trace_id: extract_trace_id(trace_id),
            span_id: extract_span_id(span_id),
            meta: Some(meta_dict),
            metrics: Some(metrics_dict),
            baggage: Some(
                baggage
                    .map(|d| d.clone().unbind())
                    .unwrap_or_else(|| PyDict::new(py).unbind()),
            ),
            span_links: Some(
                span_links
                    .map(|l| l.clone().unbind())
                    .unwrap_or_else(|| PyList::empty(py).unbind()),
            ),
            lock: Some(lock_obj),
            is_remote,
            reactivate: false,
        })
    }

    // --- trace_id ---
    #[getter]
    #[inline(always)]
    fn get_trace_id(&self) -> Option<u128> {
        self.trace_id
    }

    #[setter]
    #[inline(always)]
    fn set_trace_id(&mut self, value: Option<&Bound<'_, PyAny>>) {
        match value {
            None => self.trace_id = None,
            // Silently ignore invalid types (keep existing value), matching SpanData's setters.
            Some(_) => {
                if let Some(id) = extract_trace_id(value) {
                    self.trace_id = Some(id);
                }
            }
        }
    }

    // --- span_id ---
    #[getter]
    #[inline(always)]
    fn get_span_id(&self) -> Option<u128> {
        self.span_id
    }

    #[setter]
    #[inline(always)]
    fn set_span_id(&mut self, value: Option<&Bound<'_, PyAny>>) {
        match value {
            None => self.span_id = None,
            Some(_) => {
                if let Some(id) = extract_span_id(value) {
                    self.span_id = Some(id);
                }
            }
        }
    }

    // --- _meta ---
    #[getter(_meta)]
    #[inline(always)]
    fn get_meta<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.meta
            .get_or_insert_with(|| PyDict::new(py).unbind())
            .bind(py)
            .clone()
    }

    #[setter(_meta)]
    #[inline(always)]
    fn set_meta(&mut self, value: &Bound<'_, PyDict>) {
        self.meta = Some(value.clone().unbind());
    }

    // --- _metrics ---
    #[getter(_metrics)]
    #[inline(always)]
    fn get_metrics<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.metrics
            .get_or_insert_with(|| PyDict::new(py).unbind())
            .bind(py)
            .clone()
    }

    #[setter(_metrics)]
    #[inline(always)]
    fn set_metrics(&mut self, value: &Bound<'_, PyDict>) {
        self.metrics = Some(value.clone().unbind());
    }

    // --- _baggage ---
    #[getter(_baggage)]
    #[inline(always)]
    fn get_baggage<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.baggage
            .get_or_insert_with(|| PyDict::new(py).unbind())
            .bind(py)
            .clone()
    }

    #[setter(_baggage)]
    #[inline(always)]
    fn set_baggage(&mut self, value: &Bound<'_, PyDict>) {
        self.baggage = Some(value.clone().unbind());
    }

    // --- _span_links ---
    #[getter(_span_links)]
    #[inline(always)]
    fn get_span_links<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyList> {
        self.span_links
            .get_or_insert_with(|| PyList::empty(py).unbind())
            .bind(py)
            .clone()
    }

    #[setter(_span_links)]
    #[inline(always)]
    fn set_span_links(&mut self, value: &Bound<'_, PyList>) {
        self.span_links = Some(value.clone().unbind());
    }

    // --- _lock (read-only; nothing outside this module sets it post-construction) ---
    #[getter(_lock)]
    fn get_lock<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.lock_bound(py)
    }

    // --- sampling_priority ---
    #[getter]
    fn get_sampling_priority<'py>(
        &mut self,
        py: Python<'py>,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        let metrics = self.get_metrics(py);
        metrics.get_item(SAMPLING_PRIORITY_KEY)
    }

    #[setter]
    fn set_sampling_priority(
        &mut self,
        py: Python<'_>,
        value: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<()> {
        let lock = self.lock_bound(py)?;
        let _guard = LockGuard::acquire(lock)?;
        let metrics = self.get_metrics(py);
        match value {
            None => del_item_if_present(&metrics, SAMPLING_PRIORITY_KEY)?,
            Some(v) => metrics.set_item(SAMPLING_PRIORITY_KEY, v)?,
        }
        Ok(())
    }

    // --- dd_origin ---
    #[getter]
    fn get_dd_origin<'py>(&mut self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyAny>>> {
        let meta = self.get_meta(py);
        meta.get_item(ORIGIN_KEY)
    }

    #[setter]
    fn set_dd_origin(&mut self, py: Python<'_>, value: Option<&Bound<'_, PyAny>>) -> PyResult<()> {
        let lock = self.lock_bound(py)?;
        let _guard = LockGuard::acquire(lock)?;
        let meta = self.get_meta(py);
        match value {
            None => del_item_if_present(&meta, ORIGIN_KEY)?,
            Some(v) => meta.set_item(ORIGIN_KEY, v)?,
        }
        Ok(())
    }

    // --- dd_user_id ---
    #[getter]
    fn get_dd_user_id(&mut self, py: Python<'_>) -> PyResult<Option<String>> {
        let meta = self.get_meta(py);
        let Some(item) = meta.get_item(USER_ID_KEY)? else {
            return Ok(None);
        };
        let encoded: String = item.extract()?;
        if encoded.is_empty() {
            return Ok(None);
        }
        let decoded = BASE64
            .decode(encoded.as_bytes())
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let s = String::from_utf8(decoded).map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(Some(s))
    }

    #[setter]
    fn set_dd_user_id(&mut self, py: Python<'_>, value: Option<&str>) -> PyResult<()> {
        let lock = self.lock_bound(py)?;
        let _guard = LockGuard::acquire(lock)?;
        let meta = self.get_meta(py);
        match value {
            None => del_item_if_present(&meta, USER_ID_KEY)?,
            Some(v) => meta.set_item(USER_ID_KEY, BASE64.encode(v.as_bytes()))?,
        }
        Ok(())
    }

    // --- _trace_id_64bits ---
    #[getter]
    #[allow(non_snake_case)]
    fn get__trace_id_64bits(&self) -> Option<u128> {
        self.trace_id.map(|t| t & MAX_UINT_64BITS)
    }

    // --- _traceflags ---
    #[getter]
    #[allow(non_snake_case)]
    fn get__traceflags(&mut self, py: Python<'_>) -> PyResult<String> {
        let sp = self.sampling_priority_f64(py)?;
        Ok(if sp.map(|v| v > 0.0).unwrap_or(false) {
            "01".to_string()
        } else {
            "00".to_string()
        })
    }

    // --- _traceparent ---
    #[getter]
    #[allow(non_snake_case)]
    fn get__traceparent(&mut self, py: Python<'_>) -> PyResult<String> {
        let meta = self.get_meta(py);
        let tp: Option<String> = meta
            .get_item(W3C_TRACEPARENT_KEY)?
            .map(|v| v.extract::<String>())
            .transpose()?;
        let (Some(trace_id), Some(span_id)) = (self.trace_id, self.span_id) else {
            // if we only have a traceparent then we'll forward it; if we don't have a
            // span id or trace id value we can't build a valid traceparent
            return Ok(tp.unwrap_or_default());
        };
        let trace_id_hex = match &tp {
            // grab the original traceparent trace id, not the converted value
            Some(tp) => tp.split('-').nth(1).unwrap_or_default().to_string(),
            None => format!("{:032x}", trace_id),
        };
        let flags = self.get__traceflags(py)?;
        Ok(format!("00-{}-{:016x}-{}", trace_id_hex, span_id, flags))
    }

    // --- _tracestate ---
    #[getter]
    #[allow(non_snake_case)]
    fn get__tracestate(slf: &Bound<'_, Self>) -> PyResult<String> {
        let py = slf.py();
        let dd_list_member: String = w3c_get_dd_list_member_fn(py)?.call1((slf,))?.extract()?;
        let ts = {
            let mut this = slf.borrow_mut();
            let meta = this.get_meta(py);
            meta.get_item(W3C_TRACESTATE_KEY)?
                .map(|v| v.extract::<String>())
                .transpose()?
                .unwrap_or_default()
        };
        if !ts.is_empty() && !dd_list_member.is_empty() {
            // cut out the original dd list member from tracestate so we can replace it
            // with the new one we created
            let ts_w_out_dd: String = ts
                .split(',')
                .filter(|segment| !segment.starts_with("dd="))
                .collect::<Vec<_>>()
                .join(",");
            if ts_w_out_dd.is_empty() {
                Ok(format!("dd={}", dd_list_member))
            } else {
                Ok(format!("dd={},{}", dd_list_member, ts_w_out_dd))
            }
        } else if !dd_list_member.is_empty() {
            Ok(format!("dd={}", dd_list_member))
        } else {
            Ok(ts)
        }
    }

    // --- baggage helpers ---
    fn set_baggage_item(
        &mut self,
        py: Python<'_>,
        key: &str,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let lock = self.lock_bound(py)?;
        let _guard = LockGuard::acquire(lock)?;
        self.get_baggage(py).set_item(key, value)
    }

    fn get_baggage_item<'py>(
        &mut self,
        py: Python<'py>,
        key: &str,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        self.get_baggage(py).get_item(key)
    }

    fn get_all_baggage_items<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.get_baggage(py)
    }

    fn remove_baggage_item(&mut self, py: Python<'_>, key: &str) -> PyResult<()> {
        let lock = self.lock_bound(py)?;
        let _guard = LockGuard::acquire(lock)?;
        del_item_if_present(&self.get_baggage(py), key)
    }

    fn remove_all_baggage_items(&mut self, py: Python<'_>) -> PyResult<()> {
        let lock = self.lock_bound(py)?;
        let _guard = LockGuard::acquire(lock)?;
        self.get_baggage(py).clear();
        Ok(())
    }

    // --- copy / _with_baggage_item ---

    /// Return a shallow copy of the context with the given correlation IDs.
    ///
    /// PERF: run once per child span. Builds via the runtime type's constructor,
    /// passing the shared `_meta`/`_metrics`/`_baggage`/lock references straight into
    /// native construction, trusting that this data has already been validated.
    #[pyo3(signature = (trace_id, span_id))]
    fn copy<'py>(
        slf: &Bound<'py, Self>,
        trace_id: &Bound<'py, PyAny>,
        span_id: &Bound<'py, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let py = slf.py();
        let (meta, metrics, baggage, lock);
        {
            let mut this = slf.borrow_mut();
            let l = this.lock_bound(py)?;
            let _guard = LockGuard::acquire(l.clone())?;
            meta = this.get_meta(py);
            metrics = this.get_metrics(py);
            baggage = this.get_baggage(py);
            lock = l;
        }
        let cls = slf.get_type();
        let kwargs = PyDict::new(py);
        kwargs.set_item("trace_id", trace_id)?;
        kwargs.set_item("span_id", span_id)?;
        kwargs.set_item("meta", meta)?;
        kwargs.set_item("metrics", metrics)?;
        kwargs.set_item("baggage", baggage)?;
        kwargs.set_item("lock", lock)?;
        kwargs.set_item("is_remote", false)?;
        Ok(cls.call((), Some(&kwargs))?.unbind())
    }

    /// Returns a copy of this context with a new baggage item. Unused internally
    /// (no callers in-repo) but kept for API parity with the pure-Python original.
    fn _with_baggage_item<'py>(
        slf: &Bound<'py, Self>,
        key: &Bound<'py, PyAny>,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let py = slf.py();
        let (trace_id, span_id, meta, metrics, new_baggage);
        {
            let mut this = slf.borrow_mut();
            let baggage = this.get_baggage(py);
            new_baggage = baggage.copy()?;
            new_baggage.set_item(key, value)?;
            meta = this.get_meta(py);
            metrics = this.get_metrics(py);
            trace_id = this.trace_id;
            span_id = this.span_id;
        }
        let cls = slf.get_type();
        let kwargs = PyDict::new(py);
        kwargs.set_item("trace_id", trace_id)?;
        kwargs.set_item("span_id", span_id)?;
        let new_ctx = cls.call((), Some(&kwargs))?;
        new_ctx.setattr("_meta", meta)?;
        new_ctx.setattr("_metrics", metrics)?;
        new_ctx.setattr("_baggage", new_baggage)?;
        Ok(new_ctx.unbind())
    }

    // --- context manager protocol ---
    fn __enter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, Self>> {
        let py = slf.py();
        let lock = slf.borrow_mut().lock_bound(py)?;
        lock.call_method0("acquire")?;
        Ok(slf.clone())
    }

    #[pyo3(signature = (*_args))]
    fn __exit__(slf: &Bound<'_, Self>, _args: &Bound<'_, PyTuple>) -> PyResult<()> {
        let py = slf.py();
        let lock = slf.borrow_mut().lock_bound(py)?;
        lock.call_method0("release")?;
        Ok(())
    }

    // --- equality / repr / hash ---

    // NOTE: span_id/_reactivate are deliberately excluded. A Context compares equal
    // to any per-span copy() of itself (differing only in span_id) -- e.g.
    // Span.context builds one such copy per child span -- so equality here means
    // "same trace-level state", not "same span".
    fn __eq__(slf: &Bound<'_, Self>, other: &Bound<'_, PyAny>) -> PyResult<bool> {
        let py = slf.py();
        if !other.is_instance_of::<Self>() {
            return Ok(false);
        }
        let other = other.cast::<Self>()?;
        let lock = slf.borrow_mut().lock_bound(py)?;
        let _guard = LockGuard::acquire(lock)?;

        if slf.borrow().trace_id != other.borrow().trace_id {
            return Ok(false);
        }
        // Each side's `borrow_mut()` is taken in its own statement (rather than both
        // within one expression) so the guards never overlap -- `slf` and `other` may
        // alias the same underlying object (e.g. comparing a Context to itself), and
        // Rust would otherwise extend both temporaries' lifetimes to the end of the
        // enclosing statement, causing a double mutable borrow / PyBorrowMutError panic.
        let self_meta = slf.borrow_mut().get_meta(py);
        let other_meta = other.borrow_mut().get_meta(py);
        if !self_meta.eq(other_meta)? {
            return Ok(false);
        }
        let self_metrics = slf.borrow_mut().get_metrics(py);
        let other_metrics = other.borrow_mut().get_metrics(py);
        if !self_metrics.eq(other_metrics)? {
            return Ok(false);
        }
        let self_span_links = slf.borrow_mut().get_span_links(py);
        let other_span_links = other.borrow_mut().get_span_links(py);
        if !self_span_links.eq(other_span_links)? {
            return Ok(false);
        }
        let self_baggage = slf.borrow_mut().get_baggage(py);
        let other_baggage = other.borrow_mut().get_baggage(py);
        if !self_baggage.eq(other_baggage)? {
            return Ok(false);
        }
        Ok(slf.borrow().is_remote == other.borrow().is_remote)
    }

    fn __repr__(slf: &Bound<'_, Self>) -> PyResult<String> {
        let py = slf.py();
        let (trace_id, span_id, is_remote) = {
            let this = slf.borrow();
            (this.trace_id, this.span_id, this.is_remote)
        };
        let meta = slf.borrow_mut().get_meta(py);
        let metrics = slf.borrow_mut().get_metrics(py);
        let span_links = slf.borrow_mut().get_span_links(py);
        let baggage = slf.borrow_mut().get_baggage(py);
        Ok(format!(
            "Context(trace_id={}, span_id={}, _meta={}, _metrics={}, _span_links={}, _baggage={}, _is_remote={})",
            trace_id.map_or_else(|| "None".to_string(), |v| v.to_string()),
            span_id.map_or_else(|| "None".to_string(), |v| v.to_string()),
            meta.repr()?,
            metrics.repr()?,
            span_links.repr()?,
            baggage.repr()?,
            if is_remote { "True" } else { "False" },
        ))
    }

    fn __hash__(&self, py: Python<'_>) -> PyResult<isize> {
        match self.trace_id {
            Some(t) => t.into_pyobject(py).expect("u128 into_pyobject").hash(),
            None => py.None().bind(py).hash(),
        }
    }

    // --- pickling ---
    fn __getstate__<'py>(&mut self, py: Python<'py>) -> ContextState<'py> {
        (
            self.trace_id,
            self.span_id,
            self.get_meta(py),
            self.get_metrics(py),
            self.get_span_links(py),
            self.get_baggage(py),
            self.is_remote,
            self.reactivate,
        )
    }

    fn __setstate__(&mut self, py: Python<'_>, state: &Bound<'_, PyTuple>) -> PyResult<()> {
        self.trace_id = state.get_item(0)?.extract()?;
        self.span_id = state.get_item(1)?.extract()?;
        self.meta = Some(state.get_item(2)?.extract::<Bound<'_, PyDict>>()?.unbind());
        self.metrics = Some(state.get_item(3)?.extract::<Bound<'_, PyDict>>()?.unbind());
        self.span_links = Some(state.get_item(4)?.extract::<Bound<'_, PyList>>()?.unbind());
        self.baggage = Some(state.get_item(5)?.extract::<Bound<'_, PyDict>>()?.unbind());
        self.is_remote = state.get_item(6)?.extract()?;
        self.reactivate = state.get_item(7)?.extract()?;
        // `_lock` is not serializable -- recreate it fresh, same as the pure-Python original.
        self.lock = Some(rlock_class(py)?.call0()?.unbind());
        Ok(())
    }

    // --- Cyclic GC support ---
    //
    // `_meta`/`_metrics`/`_baggage`/`_span_links` are Python containers whose
    // contents are arbitrary user data (e.g. a baggage value can reference
    // something that references this Context). Without `__traverse__`/`__clear__`
    // such a cycle is invisible to CPython's cyclic GC and leaks forever -- see
    // the identical rationale on `SpanData`, which hit exactly this class of bug
    // for `meta_struct`.
    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        if let Some(d) = &self.meta {
            visit.call(d)?;
        }
        if let Some(d) = &self.metrics {
            visit.call(d)?;
        }
        if let Some(d) = &self.baggage {
            visit.call(d)?;
        }
        if let Some(l) = &self.span_links {
            visit.call(l)?;
        }
        // Deliberately not traversed: `_thread.RLock` is a leaf builtin (no `__dict__`,
        // no references back into user code), so it cannot participate in a cycle.
        Ok(())
    }

    fn __clear__(&mut self) {
        // Reset to Default to drop every owned Python reference so CPython can
        // break cycles. See `SpanData::__clear__` for the identical rationale.
        *self = Self::default();
    }
}
