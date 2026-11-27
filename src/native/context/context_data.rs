use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::OnceLock;

use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    intern,
    types::{
        PyAny, PyAnyMethods as _, PyDict, PyDictMethods as _, PyList, PyString,
        PyStringMethods as _, PyTuple,
    },
    Bound, IntoPyObject, Py, PyResult, Python,
};

use crate::profiler_layout::{field_offset, span_types, ContextOffsets, CopyRange};

const ORIGIN_KEY: &str = "_dd.origin";
const SAMPLING_PRIORITY_KEY: &str = "_sampling_priority_v1";
const USER_ID_KEY: &str = "_dd.p.usr.id";
const W3C_TRACEPARENT_KEY: &str = "traceparent";
const W3C_TRACESTATE_KEY: &str = "tracestate";
const MAX_UINT_64BITS: u128 = (1u128 << 64) - 1;
// Keep in sync with ddtrace/internal/datadog/profiling/context_meta.py.
const PROFILING_LOCAL_ROOT_SPAN_ID_KEY: &str = "_dd.profiling.local_root_span_id";
const PROFILING_SPAN_TYPE_KEY: &str = "_dd.profiling.span_type";

type ContextState<'py> = (
    Option<u128>,
    Option<u128>,
    Bound<'py, PyDict>,
    Bound<'py, PyDict>,
    Bound<'py, PyList>,
    Bound<'py, PyDict>,
    bool,
    bool,
    Option<f64>,
);

/// Python helpers used to build and normalize tracestate. Python owns them (and the
/// tracestate size limit) and hands them over through `Context._register_tracestate_helpers`,
/// so this crate never imports ddtrace modules itself.
struct TracestateHelpers {
    w3c_get_dd_list_member: Py<PyAny>,
    w3c_build_tracestate_members: Py<PyAny>,
    normalize_otel_tracestate: Py<PyAny>,
    materialize_otel_sampling_decision: Py<PyAny>,
    tracestate_max_bytes: usize,
}

static TRACESTATE_HELPERS: OnceLock<TracestateHelpers> = OnceLock::new();

fn tracestate_helpers() -> PyResult<&'static TracestateHelpers> {
    TRACESTATE_HELPERS.get().ok_or_else(|| {
        PyRuntimeError::new_err(
            "tracestate helpers are not registered: \
             Context._register_tracestate_helpers() must be called before building a tracestate",
        )
    })
}

#[inline]
fn is_printable_ascii(s: &str) -> bool {
    s.chars().all(|c| (0x20..=0x7E).contains(&(c as u32)))
}

#[inline]
fn del_item_if_present(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<()> {
    if dict.contains(key)? {
        dict.del_item(key)?;
    }
    Ok(())
}

#[inline]
fn extract_trace_id(v: Option<&Bound<'_, PyAny>>) -> Option<u128> {
    v.and_then(|v| v.extract::<u128>().ok())
}

/// The profiler link stored in a `_meta` dict, as typed values.
///
/// Mirrors `read_profiler_link` in `ddtrace/internal/datadog/profiling/context_meta.py`: the
/// local root span id is the hex value of `_dd.profiling.local_root_span_id` (0 when missing,
/// empty or not hex) and the span type is the id of `_dd.profiling.span_type` (0 when missing
/// or empty). Ids keep their low 64 bits, as the profiler does.
#[derive(Clone, Copy, Default, PartialEq, Eq, Debug)]
struct ProfilerLink {
    local_root_span_id: u64,
    span_type_id: u32,
}

impl ProfilerLink {
    fn from_meta<'py>(meta: &Bound<'py, PyDict>) -> Self {
        if meta.is_empty() {
            return Self::default();
        }
        let py = meta.py();
        let str_item = |key: &Bound<'py, PyString>| -> Option<Bound<'py, PyString>> {
            meta.get_item(key)
                .ok()
                .flatten()
                .and_then(|v| v.cast_into::<PyString>().ok())
        };
        let local_root_span_id = str_item(intern!(py, PROFILING_LOCAL_ROOT_SPAN_ID_KEY))
            .and_then(|v| v.to_str().ok().and_then(parse_hex_id))
            .unwrap_or(0);
        let span_type_id = str_item(intern!(py, PROFILING_SPAN_TYPE_KEY))
            .and_then(|v| v.to_str().ok().map(span_types::intern))
            .unwrap_or(span_types::NONE_ID);
        Self {
            local_root_span_id,
            span_type_id,
        }
    }
}

/// Parse a hex span id as `int(value, 16)` does for the values the tracer writes, keeping the
/// low 64 bits.
fn parse_hex_id(value: &str) -> Option<u64> {
    let value = value.trim();
    let value = value
        .strip_prefix("0x")
        .or_else(|| value.strip_prefix("0X"))
        .unwrap_or(value);
    u128::from_str_radix(value, 16).ok().map(|id| id as u64)
}

/// Low 64 bits of a span id, 0 when absent. This is what the profiler records.
#[inline(always)]
fn span_id_mirror(span_id: Option<u128>) -> u64 {
    span_id.map_or(0, |id| id as u64)
}

#[inline]
fn extract_span_id(v: Option<&Bound<'_, PyAny>>) -> Option<u128> {
    let v = v?;
    if let Ok(id) = v.extract::<u128>() {
        return Some(id);
    }
    v.extract::<&str>()
        .ok()
        .and_then(|s| s.parse::<u128>().ok())
}

#[pyo3::pyclass(name = "Context", module = "ddtrace._trace.context", weakref, subclass)]
// `repr(C)` keeps the fields that the stack profiler reads without the GIL in one contiguous
// range (`profiler_span_id` through `reactivate`). See `crate::profiler_layout`.
#[repr(C)]
pub struct Context {
    pub trace_id: Option<u128>,
    /// Write it only through `set_span_id_native`, which keeps `profiler_span_id` in sync.
    pub(crate) span_id: Option<u128>,
    // --- Fields read by the stack profiler without the GIL (start) ---
    /// Low 64 bits of `span_id`, 0 when it is `None`.
    profiler_span_id: AtomicU64,
    /// Typed copy of `_dd.profiling.local_root_span_id` in `meta`, 0 when absent.
    profiler_local_root_span_id: AtomicU64,
    /// Span type id of `_dd.profiling.span_type` in `meta`, 0 when absent.
    profiler_span_type_id: AtomicU32,
    pub reactivate: AtomicBool,
    // --- Fields read by the stack profiler without the GIL (end) ---
    #[pyo3(get, set, name = "_is_remote")]
    pub is_remote: bool,
    meta: Py<PyDict>,
    metrics: Py<PyDict>,
    baggage: Option<Py<PyDict>>,
    span_links: Option<Py<PyList>>,
    // Child contexts point the otel_sampling_state_owner at the trace's
    // owning Context. This keeps pending propagation state visible across copies
    // without allocating a holder or storing control data in meta/metrics.
    #[pyo3(get, set, name = "_otel_sampling_state_data")]
    pub otel_sampling_state_data: Option<f64>,
    #[pyo3(get, set, name = "_otel_sampling_state_owner")]
    pub otel_sampling_state_owner: Option<Py<Context>>,
}

// The profiler copies `profiler_span_id` through `reactivate` in one read. Keep it small.
const _: () = {
    use std::mem::offset_of;
    assert!(offset_of!(Context, reactivate) + 1 - offset_of!(Context, profiler_span_id) <= 32);
};

impl Default for Context {
    fn default() -> Self {
        Python::attach(|py| Context {
            trace_id: None,
            span_id: None,
            profiler_span_id: AtomicU64::new(0),
            profiler_local_root_span_id: AtomicU64::new(0),
            profiler_span_type_id: AtomicU32::new(span_types::NONE_ID),
            meta: PyDict::new(py).unbind(),
            metrics: PyDict::new(py).unbind(),
            baggage: None,
            span_links: None,
            is_remote: false,
            reactivate: AtomicBool::new(false),
            otel_sampling_state_data: None,
            otel_sampling_state_owner: None,
        })
    }
}

impl Context {
    /// Set `span_id` and keep its profiler mirror in sync.
    #[inline(always)]
    fn set_span_id_native(&mut self, span_id: Option<u128>) {
        self.span_id = span_id;
        self.profiler_span_id
            .store(span_id_mirror(span_id), Ordering::Release);
    }

    #[inline(always)]
    fn profiler_link(&self) -> ProfilerLink {
        ProfilerLink {
            local_root_span_id: self.profiler_local_root_span_id.load(Ordering::Relaxed),
            span_type_id: self.profiler_span_type_id.load(Ordering::Relaxed),
        }
    }

    /// Set `meta` and the typed profiler link read from it. Returns the old `meta`, so the
    /// caller can drop it after releasing its borrow.
    #[must_use]
    fn set_meta_native(&mut self, meta: Py<PyDict>, link: ProfilerLink) -> Py<PyDict> {
        self.profiler_local_root_span_id
            .store(link.local_root_span_id, Ordering::Release);
        self.profiler_span_type_id
            .store(link.span_type_id, Ordering::Release);
        std::mem::replace(&mut self.meta, meta)
    }

    /// Byte offsets of the fields the stack profiler reads, measured on a live object.
    pub(crate) fn profiler_offsets(py: Python<'_>) -> PyResult<ContextOffsets> {
        let obj = Bound::new(py, Self::default())?;
        let base = obj.as_ptr();
        let this = obj.borrow();
        let span_id = field_offset(base, &this.profiler_span_id);
        let local_root_span_id = field_offset(base, &this.profiler_local_root_span_id);
        let span_type_id = field_offset(base, &this.profiler_span_type_id);
        let reactivate = field_offset(base, &this.reactivate);
        let range = CopyRange::of(&[
            (span_id, size_of::<AtomicU64>()),
            (local_root_span_id, size_of::<AtomicU64>()),
            (span_type_id, size_of::<AtomicU32>()),
            (reactivate, size_of::<AtomicBool>()),
        ]);
        Ok(ContextOffsets {
            copy_offset: range.offset,
            copy_size: range.size,
            span_id,
            local_root_span_id,
            span_type_id,
            reactivate,
        })
    }

    fn sampling_priority_f64(&mut self, py: Python<'_>) -> PyResult<Option<f64>> {
        let metrics = self.get_metrics(py);
        match metrics.get_item(SAMPLING_PRIORITY_KEY)? {
            Some(v) => Ok(Some(v.extract::<f64>()?)),
            None => Ok(None),
        }
    }

    fn trace_flags(&mut self, py: Python<'_>) -> PyResult<u8> {
        let meta = self.get_meta(py);
        let inherited_flags = meta
            .get_item(W3C_TRACEPARENT_KEY)?
            .and_then(|value| value.extract::<String>().ok())
            .and_then(|value| {
                value
                    .get(53..55)
                    .and_then(|flags| u8::from_str_radix(flags, 16).ok())
            })
            .unwrap_or(0)
            & 0x2;
        let sampled = self
            .sampling_priority_f64(py)?
            .map(|priority| priority > 0.0)
            .unwrap_or(false) as u8;
        Ok(inherited_flags | sampled)
    }

    fn effective_otel_sampling_state(slf: &Bound<'_, Self>) -> Option<f64> {
        let py = slf.py();
        let owner = slf
            .borrow()
            .otel_sampling_state_owner
            .as_ref()
            .map(|owner| owner.clone_ref(py));
        match owner {
            Some(owner) => owner.bind(py).borrow().otel_sampling_state_data,
            None => slf.borrow().otel_sampling_state_data,
        }
    }

    fn set_otel_sampling_state(slf: &Bound<'_, Self>, value: Option<f64>) {
        let py = slf.py();
        let owner = slf
            .borrow()
            .otel_sampling_state_owner
            .as_ref()
            .map(|owner| owner.clone_ref(py));
        match owner {
            Some(owner) => owner.bind(py).borrow_mut().otel_sampling_state_data = value,
            None => slf.borrow_mut().otel_sampling_state_data = value,
        }
    }

    fn materialize_otel_sampling_decision(slf: &Bound<'_, Self>) -> PyResult<()> {
        let Some(sample_rate) = Self::effective_otel_sampling_state(slf) else {
            return Ok(());
        };

        let py = slf.py();
        let (trace_id, sampled, meta) = {
            let mut this = slf.borrow_mut();
            let sampled = this
                .sampling_priority_f64(py)?
                .map(|priority| priority > 0.0)
                .unwrap_or(false);
            (this.trace_id, sampled, this.get_meta(py))
        };
        let probabilistic_decision = sample_rate >= 0.0;
        tracestate_helpers()?
            .materialize_otel_sampling_decision
            .bind(py)
            .call1((
                meta,
                trace_id,
                sampled,
                if probabilistic_decision {
                    sample_rate
                } else {
                    0.0
                },
                probabilistic_decision,
            ))?;
        Self::set_otel_sampling_state(slf, None);
        Ok(())
    }

    fn build_tracestate(slf: &Bound<'_, Self>, parent_id: Option<u128>) -> PyResult<Vec<String>> {
        Self::materialize_otel_sampling_decision(slf)?;
        let py = slf.py();
        let mut dd_list_member: String = tracestate_helpers()?
            .w3c_get_dd_list_member
            .bind(py)
            .call1((slf,))?
            .extract()?;
        if let Some(parent_id) = parent_id {
            let parent_member = format!("p:{:016x}", parent_id);
            dd_list_member = if dd_list_member.is_empty() {
                parent_member
            } else {
                format!("{};{}", parent_member, dd_list_member)
            };
        }

        let raw_tracestate = {
            let mut this = slf.borrow_mut();
            this.get_meta(py)
                .get_item(W3C_TRACESTATE_KEY)?
                .map(|value| value.extract::<String>())
                .transpose()?
                .unwrap_or_default()
        };
        if raw_tracestate.is_empty() {
            return Ok(if dd_list_member.is_empty() {
                Vec::new()
            } else {
                vec![format!("dd={}", dd_list_member)]
            });
        }
        if raw_tracestate.starts_with("ot=")
            && !raw_tracestate.contains(',')
            && raw_tracestate.is_ascii()
        {
            if dd_list_member.is_empty() {
                return Ok(vec![raw_tracestate]);
            }
            let dd_member = format!("dd={}", dd_list_member);
            if dd_member.len() + raw_tracestate.len() < tracestate_helpers()?.tracestate_max_bytes {
                return Ok(vec![dd_member, raw_tracestate]);
            }
            return Ok(vec![dd_member]);
        }
        tracestate_helpers()?
            .w3c_build_tracestate_members
            .bind(py)
            .call1((raw_tracestate, dd_list_member))?
            .extract()
    }

    /// Native-typed sibling of the `copy` pymethod, used by `SpanData::get_context` to
    /// build a child context without a Python round-trip for `trace_id`/`span_id`.
    pub(crate) fn copy_native<'py>(
        slf: &Bound<'py, Self>,
        trace_id: Option<u128>,
        span_id: Option<u128>,
    ) -> PyResult<Py<Self>> {
        let py = slf.py();

        let mut this = slf.try_borrow_mut()?;
        let meta = this.get_meta(py).unbind();
        let metrics = this.get_metrics(py).unbind();
        // The copy shares `meta`, so it shares the profiler link read from it.
        let link = this.profiler_link();
        let baggage = Some(this.get_baggage(py).unbind());
        let otel_sampling_state_owner = Some(match &this.otel_sampling_state_owner {
            Some(owner) => owner.clone_ref(py),
            None => slf.clone().unbind(),
        });
        drop(this);
        let span_links = Some(PyList::empty(py).unbind());

        Py::new(
            py,
            Self {
                trace_id,
                span_id,
                profiler_span_id: AtomicU64::new(span_id_mirror(span_id)),
                profiler_local_root_span_id: AtomicU64::new(link.local_root_span_id),
                profiler_span_type_id: AtomicU32::new(link.span_type_id),
                meta,
                metrics,
                baggage,
                span_links,
                is_remote: false,
                reactivate: AtomicBool::new(false),
                otel_sampling_state_data: None,
                otel_sampling_state_owner,
            },
        )
    }

    /// Fresh trace-level state for a root span's `Context`, built natively (no Python
    /// round-trip) so `SpanData::get_context` can construct it directly.
    pub(crate) fn new_root(py: Python<'_>, trace_id: u128, span_id: u128) -> PyResult<Py<Self>> {
        Py::new(
            py,
            Self {
                trace_id: Some(trace_id),
                span_id: Some(span_id),
                profiler_span_id: AtomicU64::new(span_id as u64),
                profiler_local_root_span_id: AtomicU64::new(0),
                profiler_span_type_id: AtomicU32::new(span_types::NONE_ID),
                meta: PyDict::new(py).unbind(),
                metrics: PyDict::new(py).unbind(),
                baggage: Some(PyDict::new(py).unbind()),
                span_links: Some(PyList::empty(py).unbind()),
                is_remote: false,
                reactivate: AtomicBool::new(false),
                otel_sampling_state_data: None,
                otel_sampling_state_owner: None,
            },
        )
    }
}

#[pyo3::pymethods]
impl Context {
    #[new]
    #[classmethod]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        trace_id=None,
        span_id=None,
        dd_origin=None,
        sampling_priority=None,
        meta=None,
        metrics=None,
        span_links=None,
        baggage=None,
        is_remote=true,
    ))]
    pub fn __new__<'p>(
        _: &Bound<'p, pyo3::types::PyType>,
        py: Python<'p>,
        trace_id: Option<&Bound<'p, PyAny>>,
        span_id: Option<&Bound<'p, PyAny>>,
        dd_origin: Option<&Bound<'p, PyAny>>,
        sampling_priority: Option<&Bound<'p, PyAny>>,
        meta: Option<&Bound<'p, PyDict>>,
        metrics: Option<&Bound<'p, PyDict>>,
        span_links: Option<&Bound<'p, PyList>>,
        baggage: Option<&Bound<'p, PyDict>>,
        is_remote: bool,
    ) -> PyResult<Self> {
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
        if meta_dict.bind(py).contains(W3C_TRACESTATE_KEY)? {
            tracestate_helpers()?
                .normalize_otel_tracestate
                .bind(py)
                .call1((meta_dict.bind(py),))?;
        }
        // Only a caller-supplied `meta` can hold a profiler link; skip the lookups otherwise.
        let link = match meta {
            Some(_) => ProfilerLink::from_meta(meta_dict.bind(py)),
            None => ProfilerLink::default(),
        };
        let span_id = extract_span_id(span_id);
        Ok(Self {
            trace_id: extract_trace_id(trace_id),
            span_id,
            profiler_span_id: AtomicU64::new(span_id_mirror(span_id)),
            profiler_local_root_span_id: AtomicU64::new(link.local_root_span_id),
            profiler_span_type_id: AtomicU32::new(link.span_type_id),
            meta: meta_dict,
            metrics: metrics_dict,
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
            is_remote,
            reactivate: AtomicBool::new(false),
            otel_sampling_state_data: None,
            otel_sampling_state_owner: None,
        })
    }

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
            Some(_) => {
                if let Some(id) = extract_trace_id(value) {
                    self.trace_id = Some(id);
                }
            }
        }
    }

    #[getter]
    #[inline(always)]
    fn get_span_id(&self) -> Option<u128> {
        self.span_id
    }

    #[setter]
    #[inline(always)]
    fn set_span_id(&mut self, value: Option<&Bound<'_, PyAny>>) {
        match value {
            None => self.set_span_id_native(None),
            Some(_) => {
                if let Some(id) = extract_span_id(value) {
                    self.set_span_id_native(Some(id));
                }
            }
        }
    }

    #[getter(_reactivate)]
    #[inline(always)]
    fn get_reactivate(&self) -> bool {
        self.reactivate.load(Ordering::Relaxed)
    }

    #[setter(_reactivate)]
    #[inline(always)]
    fn set_reactivate(&self, value: bool) {
        self.reactivate.store(value, Ordering::Release);
    }

    #[getter(_meta)]
    #[inline(always)]
    pub(crate) fn get_meta<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.meta.bind(py).clone()
    }

    #[setter(_meta)]
    #[inline(always)]
    fn set_meta(slf: &Bound<'_, Self>, value: &Bound<'_, PyDict>) {
        // Read the dict before borrowing: a key's __eq__ can run Python code.
        let link = ProfilerLink::from_meta(value);
        let old = slf
            .borrow_mut()
            .set_meta_native(value.clone().unbind(), link);
        // Dropping the old dict can run finalizers; do it after releasing the borrow.
        drop(old);
    }

    #[getter(_metrics)]
    #[inline(always)]
    fn get_metrics<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyDict> {
        self.metrics.bind(py).clone()
    }

    #[setter(_metrics)]
    #[inline(always)]
    fn set_metrics(&mut self, value: &Bound<'_, PyDict>) {
        self.metrics = value.clone().unbind();
    }

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

    #[getter]
    pub(crate) fn get_sampling_priority<'py>(
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
        let metrics = self.get_metrics(py);
        match value {
            None => del_item_if_present(&metrics, SAMPLING_PRIORITY_KEY)?,
            Some(v) => metrics.set_item(SAMPLING_PRIORITY_KEY, v)?,
        }
        Ok(())
    }

    #[getter]
    fn get_dd_origin<'py>(&mut self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyAny>>> {
        let meta = self.get_meta(py);
        meta.get_item(ORIGIN_KEY)
    }

    #[setter]
    fn set_dd_origin(&mut self, py: Python<'_>, value: Option<&Bound<'_, PyAny>>) -> PyResult<()> {
        let meta = self.get_meta(py);
        match value {
            None => del_item_if_present(&meta, ORIGIN_KEY)?,
            Some(v) => meta.set_item(ORIGIN_KEY, v)?,
        }
        Ok(())
    }

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
        let meta = self.get_meta(py);
        match value {
            None => del_item_if_present(&meta, USER_ID_KEY)?,
            Some(v) => meta.set_item(USER_ID_KEY, BASE64.encode(v.as_bytes()))?,
        }
        Ok(())
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__trace_id_64bits(&self) -> Option<u128> {
        self.trace_id.map(|t| t & MAX_UINT_64BITS)
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__trace_flags(&mut self, py: Python<'_>) -> PyResult<u8> {
        self.trace_flags(py)
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__traceflags(&mut self, py: Python<'_>) -> PyResult<String> {
        Ok(format!("{:02x}", self.trace_flags(py)?))
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__traceparent(&mut self, py: Python<'_>) -> PyResult<String> {
        let meta = self.get_meta(py);
        let tp: Option<String> = meta
            .get_item(W3C_TRACEPARENT_KEY)?
            .map(|v| v.extract::<String>())
            .transpose()?;
        let (Some(trace_id), Some(span_id)) = (self.trace_id, self.span_id) else {
            return Ok(tp.unwrap_or_default());
        };
        let trace_id_hex = match tp.as_deref() {
            Some(tp) if !tp.is_empty() => tp.split('-').nth(1).unwrap_or_default().to_string(),
            _ => format!("{:032x}", trace_id),
        };
        Ok(format!(
            "00-{}-{:016x}-{:02x}",
            trace_id_hex,
            span_id,
            self.trace_flags(py)?
        ))
    }

    #[getter]
    #[allow(non_snake_case)]
    fn get__tracestate(slf: &Bound<'_, Self>) -> PyResult<String> {
        Ok(Self::build_tracestate(slf, None)?.join(","))
    }

    #[pyo3(signature = (parent_id=None))]
    fn _tracestate_entries(
        slf: &Bound<'_, Self>,
        parent_id: Option<u128>,
    ) -> PyResult<Vec<(String, String)>> {
        Ok(Self::build_tracestate(slf, parent_id)?
            .into_iter()
            .map(|member| {
                let (key, value) = member.split_once('=').unwrap_or((&member, ""));
                (key.to_string(), value.to_string())
            })
            .collect())
    }

    /// Register the Python helpers that build_tracestate relies on.
    ///
    /// Python calls this once at import time, which also keeps the native code from
    /// importing anything later (Temporal sandbox, etc. may block imports). The first
    /// registration wins; later calls are ignored. Building a tracestate before any
    /// registration raises RuntimeError.
    #[staticmethod]
    fn _register_tracestate_helpers(
        w3c_get_dd_list_member: Py<PyAny>,
        w3c_build_tracestate_members: Py<PyAny>,
        normalize_otel_tracestate: Py<PyAny>,
        materialize_otel_sampling_decision: Py<PyAny>,
        tracestate_max_bytes: usize,
    ) {
        let _ = TRACESTATE_HELPERS.set(TracestateHelpers {
            w3c_get_dd_list_member,
            w3c_build_tracestate_members,
            normalize_otel_tracestate,
            materialize_otel_sampling_decision,
            tracestate_max_bytes,
        });
    }

    pub(crate) fn _publish_sampling_decision(
        slf: &Bound<'_, Self>,
        sampling_priority: Option<&Bound<'_, PyAny>>,
        sample_rate: f64,
        probabilistic_decision: bool,
    ) -> PyResult<()> {
        Self::set_otel_sampling_state(
            slf,
            Some(if probabilistic_decision && sample_rate > 0.0 {
                sample_rate
            } else {
                -1.0
            }),
        );
        let py = slf.py();
        let metrics = slf.borrow_mut().get_metrics(py);
        match sampling_priority {
            Some(priority) => metrics.set_item(SAMPLING_PRIORITY_KEY, priority)?,
            None => del_item_if_present(&metrics, SAMPLING_PRIORITY_KEY)?,
        }
        Ok(())
    }

    fn set_baggage_item(
        &mut self,
        py: Python<'_>,
        key: &str,
        value: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
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
        del_item_if_present(&self.get_baggage(py), key)
    }

    fn remove_all_baggage_items(&mut self, py: Python<'_>) -> PyResult<()> {
        self.get_baggage(py).clear();
        Ok(())
    }

    /// PERF: run once per child span. Constructs a plain `Context` directly via
    /// the Rust struct, bypassing any subclass `__new__`/`__init__`, and reuses
    /// the shared `_meta`/`_metrics`/`_baggage` references, trusting that this
    /// data has already been validated. This mirrors the previous Python
    /// implementation's `Context.__new__(Context)` behavior.
    #[pyo3(signature = (trace_id, span_id))]
    fn copy<'py>(
        slf: &Bound<'py, Self>,
        trace_id: &Bound<'py, PyAny>,
        span_id: &Bound<'py, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        Ok(Self::copy_native(
            slf,
            extract_trace_id(Some(trace_id)),
            extract_span_id(Some(span_id)),
        )?
        .into_any())
    }

    fn _with_baggage_item<'py>(
        slf: &Bound<'py, Self>,
        key: &Bound<'py, PyAny>,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<Py<PyAny>> {
        let py = slf.py();
        let (trace_id, span_id, meta, metrics, new_baggage, otel_sampling_state_owner);
        {
            let mut this = slf.borrow_mut();
            let baggage = this.get_baggage(py);
            new_baggage = baggage.copy()?;
            new_baggage.set_item(key, value)?;
            meta = this.get_meta(py);
            metrics = this.get_metrics(py);
            trace_id = this.trace_id;
            span_id = this.span_id;
            otel_sampling_state_owner = this
                .otel_sampling_state_owner
                .as_ref()
                .map(|owner| owner.clone_ref(py))
                .unwrap_or_else(|| slf.clone().unbind());
        }
        let cls = slf.get_type();
        let kwargs = PyDict::new(py);
        kwargs.set_item("trace_id", trace_id)?;
        kwargs.set_item("span_id", span_id)?;
        let new_ctx = cls.call((), Some(&kwargs))?;
        new_ctx.setattr("_meta", meta)?;
        new_ctx.setattr("_metrics", metrics)?;
        new_ctx.setattr("_baggage", new_baggage)?;
        new_ctx.setattr("_otel_sampling_state_owner", otel_sampling_state_owner)?;
        Ok(new_ctx.unbind())
    }

    fn __enter__<'py>(slf: &Bound<'py, Self>) -> PyResult<Bound<'py, Self>> {
        Ok(slf.clone())
    }

    #[pyo3(signature = (*_args))]
    fn __exit__(_slf: &Bound<'_, Self>, _args: &Bound<'_, PyTuple>) -> PyResult<()> {
        Ok(())
    }

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

        if slf.borrow().trace_id != other.borrow().trace_id {
            return Ok(false);
        }
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
        Ok(slf.borrow().is_remote == other.borrow().is_remote
            && Self::effective_otel_sampling_state(slf)
                == Self::effective_otel_sampling_state(other))
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

    fn __getstate__<'py>(slf: &Bound<'py, Self>) -> ContextState<'py> {
        let py = slf.py();
        let otel_sampling_state = Self::effective_otel_sampling_state(slf);
        let mut this = slf.borrow_mut();
        (
            this.trace_id,
            this.span_id,
            this.get_meta(py),
            this.get_metrics(py),
            this.get_span_links(py),
            this.get_baggage(py),
            this.is_remote,
            this.reactivate.load(Ordering::Relaxed),
            otel_sampling_state,
        )
    }

    fn __setstate__(&mut self, _py: Python<'_>, state: &Bound<'_, PyTuple>) -> PyResult<()> {
        self.trace_id = state.get_item(0)?.extract()?;
        self.set_span_id_native(state.get_item(1)?.extract()?);
        // The profiler link is not part of the state: it is read back from `meta`.
        let meta = state.get_item(2)?.extract::<Bound<'_, PyDict>>()?;
        let link = ProfilerLink::from_meta(&meta);
        drop(self.set_meta_native(meta.unbind(), link));
        self.metrics = state.get_item(3)?.extract::<Bound<'_, PyDict>>()?.unbind();
        self.span_links = Some(state.get_item(4)?.extract::<Bound<'_, PyList>>()?.unbind());
        self.baggage = Some(state.get_item(5)?.extract::<Bound<'_, PyDict>>()?.unbind());
        self.is_remote = state.get_item(6)?.extract()?;
        self.reactivate
            .store(state.get_item(7)?.extract()?, Ordering::Release);
        self.otel_sampling_state_data = if state.len()? > 8 {
            state.get_item(8)?.extract()?
        } else {
            None
        };
        self.otel_sampling_state_owner = None;
        Ok(())
    }

    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        visit.call(&self.meta)?;
        visit.call(&self.metrics)?;
        if let Some(d) = &self.baggage {
            visit.call(d)?;
        }
        if let Some(l) = &self.span_links {
            visit.call(l)?;
        }
        if let Some(owner) = &self.otel_sampling_state_owner {
            visit.call(owner)?;
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        *self = Self::default();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_hex_id_matches_written_values() {
        assert_eq!(parse_hex_id("0000000000000abc"), Some(0xabc));
        assert_eq!(parse_hex_id(" ABC "), Some(0xabc));
        assert_eq!(parse_hex_id("0xff"), Some(0xff));
        assert_eq!(parse_hex_id("ffffffffffffffff"), Some(u64::MAX));
        // Ids keep their low 64 bits, as the profiler does.
        assert_eq!(parse_hex_id("1ffffffffffffffff"), Some(u64::MAX));
        assert_eq!(parse_hex_id(""), None);
        assert_eq!(parse_hex_id("not hex"), None);
    }

    #[test]
    fn span_id_mirror_keeps_low_bits() {
        assert_eq!(span_id_mirror(None), 0);
        assert_eq!(span_id_mirror(Some(7)), 7);
        assert_eq!(span_id_mirror(Some((1u128 << 64) + 7)), 7);
    }
}
