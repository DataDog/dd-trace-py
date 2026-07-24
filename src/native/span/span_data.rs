use pyo3::{
    types::{
        PyAnyMethods as _, PyBool, PyBytes, PyBytesMethods as _, PyDict, PyDictMethods as _,
        PyFloat, PyFloatMethods as _, PyList, PyListMethods as _, PyMapping, PyMappingMethods as _,
        PyString, PyStringMethods as _, PyTuple,
    },
    Bound, IntoPyObject as _, Py, PyAny, PyResult, Python,
};

use super::attributes::{AttrKey, MetaMap, MetricValue, MetricsMap};
use crate::ddtrace_utils::flatten_key_value_vec as flatten_key_value_vec_fn;
use crate::get_or_init;
use crate::py_string::{Bytes, PyBackedString, PyTraceData};
use libdd_trace_utils::span::{
    v04::{
        AttributeAnyValue, AttributeArrayValue, SpanEvent as NativeSpanEvent,
        SpanLink as NativeSpanLink, VecMap,
    },
    SpanText as _,
};

use super::utils::{
    extract_backed_string_or_default, extract_backed_string_or_none, extract_i32_or_default,
    extract_i64_or_default, extract_time_unix_nano, wall_clock_ns,
};
use super::{SpanEvent, SpanLink};
use std::sync::OnceLock;

/// Cyclic-GC traversal for a wire span buffered inside `TraceExporterPy`.
///
/// The buffer holds fully-built `v04::Span<PyTraceData>`s (see `TraceExporterPy::put_trace`)
/// whose every string/bytes field is a [`PyBackedString`]/[`Bytes`] owning a `Py` handle to a
/// live Python object. Those handles can close a reference cycle (span → context → tracer →
/// writer → exporter → buffer), so the GC must see every one of them — the same reason
/// `SpanData::__traverse__` visits its own fields. Missing any edge here risks an uncollectable
/// cycle (leak); over-visiting is harmless (these are atomic str/bytes objects).
pub(crate) fn traverse_v04_span(
    span: &libdd_trace_utils::span::v04::Span<PyTraceData>,
    visit: &pyo3::PyVisit<'_>,
) -> Result<(), pyo3::PyTraverseError> {
    span.service.traverse(visit)?;
    span.name.traverse(visit)?;
    span.resource.traverse(visit)?;
    span.r#type.traverse(visit)?;
    // meta: both key and value are Py-backed strings.
    for (k, v) in span.meta.iter() {
        k.traverse(visit)?;
        v.traverse(visit)?;
    }
    // metrics: only the key is Py-backed (values are f64).
    for (k, _) in span.metrics.iter() {
        k.traverse(visit)?;
    }
    // meta_struct: Py-backed key + Py-backed bytes value.
    for (k, v) in span.meta_struct.iter() {
        k.traverse(visit)?;
        v.traverse(visit)?;
    }
    for link in &span.span_links {
        link.tracestate.traverse(visit)?;
        for (k, v) in &link.attributes {
            k.traverse(visit)?;
            v.traverse(visit)?;
        }
    }
    for event in &span.span_events {
        event.name.traverse(visit)?;
        for (k, v) in &event.attributes {
            k.traverse(visit)?;
            traverse_attr_any(v, visit)?;
        }
    }
    Ok(())
}

#[pyo3::pyclass(name = "SpanData", module = "ddtrace.internal._native", subclass)]
#[derive(Default)]
pub struct SpanData {
    pub name: PyBackedString,
    pub service: PyBackedString,
    pub resource: PyBackedString,
    pub span_type: PyBackedString,
    pub trace_id: u128,
    pub span_id: u64,
    pub parent_id: u64,
    pub start: i64,
    /// `None` means "not finished" (duration not yet set). Internal only — the
    /// Python-facing `duration`/`duration_ns` getters surface this as `None`/seconds.
    pub duration: Option<i64>,
    pub error: i32,
    pub span_links: Vec<NativeSpanLink<PyTraceData>>,
    pub span_events: Vec<NativeSpanEvent<PyTraceData>>,
    pub span_api: PyBackedString,
    /// String-valued tags — the source of truth for `meta`. Values keep their original
    /// `Py<PyString>`; the v0.4 wire `meta` (a `PyBackedString` view) is materialized from this
    /// map at encode time by `build_v04_span`.
    /// Mutually exclusive with `metrics`: a key lives in exactly one of the two;
    /// `insert_meta`/`insert_metric`/`remove_attribute` enforce this invariant.
    pub(crate) meta: MetaMap,
    /// Numeric-valued tags — the source of truth for `metrics`.
    /// Mutually exclusive with `meta` (see above).
    pub(crate) metrics: MetricsMap,
    /// Lazy Python int cache for the `trace_id` getter.
    /// Populated on first read; invalidated on every write to `trace_id`.
    /// `trace_id` is always the source of truth.
    pub _trace_id_py: Option<Py<PyAny>>,
    /// Storage for meta_struct values: dict[str, Any].
    /// None until first use; initialized to an empty dict in __new__.
    pub meta_struct: Option<Py<PyDict>>,
}

impl SpanData {
    /// Set `trace_id` and invalidate `_trace_id_py`.
    ///
    /// **All writes to `trace_id` must go through this method** to keep `_trace_id_py`
    /// consistent. Bypassing it leaves a stale cached Python int that silently returns the
    /// old value on the next `span.trace_id` read.
    #[inline(always)]
    pub fn set_trace_id_native(&mut self, id: u128) {
        self.trace_id = id;
        self._trace_id_py = None;
    }

    /// Setdefault helper for `_set_default_attributes`: insert one key/value pair only if
    /// the key is not already present in either meta or metrics.
    fn set_default_attribute_entry(&mut self, k: &Bound<'_, PyAny>, v: &Bound<'_, PyAny>) {
        if !self.has_attribute(k) {
            let _ = self.set_attribute(k, v);
        }
    }

    /// Insert into `meta`, evicting any existing `metrics` entry for the same key so the two
    /// maps stay mutually exclusive.
    fn insert_meta(&mut self, key: AttrKey, value: Py<PyString>) {
        self.metrics.remove(key.as_str());
        self.meta.insert(key, value);
    }

    /// Insert into `metrics`, evicting any existing `meta` entry for the same key so the two
    /// maps stay mutually exclusive.
    fn insert_metric(&mut self, key: AttrKey, value: MetricValue) {
        self.meta.remove(key.as_str());
        self.metrics.insert(key, value);
    }

    /// Build the libdatadog v0.4 wire span directly from this span's live `meta`/`metrics`
    /// (and links/events/meta_struct), materializing the `PyBackedString` wire views here.
    ///
    /// The wire span is produced in a single pass under the GIL.
    ///
    /// This is a **read**, not a drain: `self` is left fully intact (keys are `clone_ref`'d,
    /// links/events are cloned) so `get_tag`/`get_metric`/`has_attribute` still work after the
    /// finished span has been handed to the writer.
    ///
    /// `dd_origin` (`trace[0].context.dd_origin`) is injected as `_dd.origin` into every span's
    /// meta — the native attribute store only carries it on the chunk-root span. Not truncated:
    /// `_dd.origin` is an internal reserved tag, exempt from the user-tag length cap.
    ///
    /// `encode_links_as_json`/`encode_events_as_json`: true for v0.5 output, which has no wire
    /// fields for span links/events, so they're JSON-encoded into
    /// `meta[SPAN_LINKS_KEY]`/`meta[SPAN_EVENTS_KEY]` instead. `encode_events_as_json` is also
    /// true on v0.4 when the agent hasn't opted into native span events; span links have no such
    /// gate. `has_packb` gates `meta_struct` packing entirely when no packer is available.
    ///
    // AIDEV-NOTE: json.dumps (links/events) and packb (meta_struct) are Python calls that run
    // here, under the GIL and under the caller's `PyRef<SpanData>` borrow. A concurrent
    // `&mut self` on the *same* finished span during a GIL-yield in one of those calls would
    // panic pyo3's borrow flag — but spans handed to the writer are finished and never
    // concurrently mutated, so nothing can take `&mut self` while these calls run.
    pub(crate) fn build_v04_span(
        &self,
        py: Python<'_>,
        dd_origin: Option<&PyBackedString>,
        encode_links_as_json: bool,
        encode_events_as_json: bool,
        has_packb: bool,
    ) -> PyResult<libdd_trace_utils::span::v04::Span<PyTraceData>> {
        // `duration` is only None for unfinished spans, which the writer never receives; -1 is
        // a defensive fallback sentinel (a real duration is always >= 0) since v0.4's wire
        // `Span.duration` is a non-optional i64 with no representation for "unset".
        let mut out = libdd_trace_utils::span::v04::Span::<PyTraceData> {
            trace_id: self.trace_id,
            span_id: self.span_id,
            parent_id: self.parent_id,
            start: self.start,
            duration: self.duration.unwrap_or(-1),
            error: self.error,
            name: truncate_span_text(self.name.clone_ref(py)),
            service: truncate_span_text(self.service.clone_ref(py)),
            resource: truncate_span_text(self.resource.clone_ref(py)),
            r#type: truncate_span_text(self.span_type.clone_ref(py)),
            ..Default::default()
        };

        // Pre-size meta and metrics *separately* so neither grows-and-reallocs while filling.
        // meta gets a few extras: `_dd.origin` + whichever of the v0.5 links/events JSON keys
        // apply. metrics is sized to exactly the numeric-tag count.
        let links_as_json = encode_links_as_json && !self.span_links.is_empty();
        let events_as_json = encode_events_as_json && !self.span_events.is_empty();
        let extra_meta =
            links_as_json as usize + events_as_json as usize + dd_origin.is_some() as usize;
        out.meta = VecMap::with_capacity(self.meta.len() + extra_meta);
        out.metrics = VecMap::with_capacity(self.metrics.len());

        // meta: clone_ref the key and project the stored `Py<PyString>` into a GIL-free-readable
        // `PyBackedString` wire value, truncating both.
        for (key, value) in self.meta.iter() {
            // A stored str carrying lone surrogates has no valid UTF-8 wire representation;
            // skip it rather than emit bytes the msgpack payload can't legally carry.
            let Ok(value) = PyBackedString::try_from(value.bind(py).clone()) else {
                continue;
            };
            out.meta.insert(
                truncate_span_text(key.clone_ref(py)),
                truncate_span_text(value),
            );
        }

        // metrics: project Int/Float into the wire f64.
        for (key, value) in self.metrics.iter() {
            out.metrics
                .insert(truncate_span_text(key.clone_ref(py)), value.as_f64());
        }

        // Links/events: JSON-encode into meta for v0.5 (no wire field), else clone the native
        // structures (never drain — self stays intact). json.dumps is a Python call, under GIL.
        if links_as_json {
            let json = json_dumps_list(
                py,
                self.span_links
                    .iter()
                    .map(|l| span_link_to_json_dict(py, l)),
            )?;
            out.meta.insert(
                PyBackedString::from_static_str(SPAN_LINKS_KEY),
                truncate_span_text(json),
            );
        } else if !encode_links_as_json {
            out.span_links = self
                .span_links
                .iter()
                .map(|l| clone_truncate_span_link(py, l))
                .collect();
        }
        if events_as_json {
            let json = json_dumps_list(
                py,
                self.span_events
                    .iter()
                    .map(|e| span_event_to_json_dict(py, e)),
            )?;
            out.meta.insert(
                PyBackedString::from_static_str(SPAN_EVENTS_KEY),
                truncate_span_text(json),
            );
        } else if !encode_events_as_json {
            out.span_events = self
                .span_events
                .iter()
                .map(|e| clone_truncate_span_event(py, e))
                .collect();
        }

        // Inject the trace-level `_dd.origin` into every span's meta. Not truncated: it's an
        // internal reserved tag, exempt from the user-tag length cap.
        if let Some(origin) = dd_origin {
            out.meta.insert(
                PyBackedString::from_static_str(ORIGIN_KEY),
                origin.clone_ref(py),
            );
        }

        // meta_struct: pack each user dict value via ddtrace's `packb` (a Python call, under the
        // GIL). Iterate (don't take) so post-finish `_get_struct_tag` still works.
        if has_packb {
            if let (Some(meta_struct), Some(packb)) = (&self.meta_struct, get_packb(py)) {
                for (k, v) in meta_struct.bind(py).iter() {
                    let Ok(key_backed) = k.extract::<PyBackedString>() else {
                        continue;
                    };
                    let Ok(result) = packb.call1(py, (v,)) else {
                        continue;
                    };
                    let Ok(py_bytes) = result.bind(py).cast::<PyBytes>() else {
                        continue;
                    };
                    out.meta_struct.insert(
                        truncate_span_text(key_backed),
                        Bytes::from_py_bytes(py_bytes),
                    );
                }
            }
        }

        // Every wire-map key came from a unique `VecStore` row (last-wins insert + meta/metrics
        // mutual exclusion) or a unique Python dict key (meta_struct), so there are no duplicate
        // keys. Assert it without scanning, making the exporter's `span.dedup()` a no-op and
        // dropping its per-map dedup `HashSet` allocation.
        out.meta.mark_deduped();
        out.metrics.mark_deduped();
        out.meta_struct.mark_deduped();

        Ok(out)
    }
}

/// `ddtrace.internal._encoding.packb`, cached to avoid a module lookup on every span build.
static PACKB: OnceLock<Py<PyAny>> = OnceLock::new();

/// Fetch (and cache) `ddtrace.internal._encoding.packb`. `None` if the encoding module isn't
/// importable, which callers treat as "no packer available" (skip meta_struct).
pub(crate) fn get_packb(py: Python<'_>) -> Option<&'static Py<PyAny>> {
    get_or_init!(PACKB, py, {
        py.import("ddtrace.internal._encoding")
            .and_then(|m| m.getattr("packb"))
            .map(|a| a.unbind())
    })
    .ok()
}

const HTTP_STATUS_CODE_KEY: &str = "http.status_code";

/// Wire key for the trace-level origin tag (mirrors `_ORIGIN_KEY` in
/// `ddtrace/internal/constants.py`).
const ORIGIN_KEY: &str = "_dd.origin";

/// Mirrors `MAX_SPAN_META_VALUE_LEN` / `TRUNCATED_SPAN_ATTRIBUTE_LEN` in
/// `ddtrace/_trace/_limits.py`. Oversized fields must be truncated here to keep wire-format
/// parity — otherwise payload size balloons and downstream consumers assume this cap holds.
const MAX_SPAN_META_VALUE_LEN: usize = 25000;
const TRUNCATED_SPAN_ATTRIBUTE_LEN: usize = 2500;
const TRUNCATED_SUFFIX: &str = "<truncated>...";

/// Truncate a string field to `MAX_SPAN_META_VALUE_LEN` *characters* (not bytes). Fast path on
/// byte length first — UTF-8 encoding is never shorter than character count, so a string within
/// the byte budget is always within the character budget too, letting the common (short, ASCII)
/// case skip the `chars().count()` scan entirely and return with **no Python/GIL interaction at
/// all** (`len`/`chars` read straight through `PyBackedString`'s raw pointer). Takes `s` by value
/// so the common untruncated case returns it straight back with no extra clone/refcount traffic.
///
/// The rare oversized-string path needs to allocate a new `PyString`, which does require the
/// GIL — `Python::attach` re-acquires it just for that (uncommon) branch. `build_v04_span`
/// always calls this under the GIL, so the fast path costs nothing extra.
fn truncate_span_text(s: PyBackedString) -> PyBackedString {
    if s.len() <= MAX_SPAN_META_VALUE_LEN || s.chars().count() <= MAX_SPAN_META_VALUE_LEN {
        return s;
    }
    let keep = TRUNCATED_SPAN_ATTRIBUTE_LEN - TRUNCATED_SUFFIX.len();
    let truncated: String = s
        .chars()
        .take(keep)
        .chain(TRUNCATED_SUFFIX.chars())
        .collect();
    Python::attach(|py| {
        PyBackedString::try_from(PyString::new(py, &truncated))
            .expect("newly created PyString is valid UTF-8")
    })
}

/// Clone (via refcount-bump `clone_ref`) and truncate every string field of a live span link in
/// a single pass: `tracestate` and each attribute key/value. Reads `link` by reference so
/// `self.span_links` stays intact for post-finish `_get_links`. Mirrors the Cython encoder's
/// `_pack_links`, which ran every link field through `pack_text`.
fn clone_truncate_span_link(
    py: Python<'_>,
    link: &NativeSpanLink<PyTraceData>,
) -> NativeSpanLink<PyTraceData> {
    NativeSpanLink {
        trace_id: link.trace_id,
        trace_id_high: link.trace_id_high,
        span_id: link.span_id,
        tracestate: truncate_span_text(link.tracestate.clone_ref(py)),
        flags: link.flags,
        attributes: link
            .attributes
            .iter()
            .map(|(k, v)| {
                (
                    truncate_span_text(k.clone_ref(py)),
                    truncate_span_text(v.clone_ref(py)),
                )
            })
            .collect(),
    }
}

/// Truncate the string payload of a single attribute value (`String` variant only —
/// booleans/numbers have no text to truncate). Mirrors `pack_span_event_attributes`.
fn truncate_attribute_array_value(
    value: AttributeArrayValue<PyTraceData>,
) -> AttributeArrayValue<PyTraceData> {
    match value {
        AttributeArrayValue::String(s) => AttributeArrayValue::String(truncate_span_text(s)),
        other => other,
    }
}

fn truncate_attribute_any_value(
    value: AttributeAnyValue<PyTraceData>,
) -> AttributeAnyValue<PyTraceData> {
    match value {
        AttributeAnyValue::SingleValue(v) => {
            AttributeAnyValue::SingleValue(truncate_attribute_array_value(v))
        }
        AttributeAnyValue::Array(vec) => AttributeAnyValue::Array(
            vec.into_iter()
                .map(truncate_attribute_array_value)
                .collect(),
        ),
    }
}

/// Clone (via refcount-bump `clone_ref`) and truncate every string field of a live span event in
/// a single pass: `name` and each attribute key/value. Reads `event` by reference so
/// `self.span_events` stays intact for post-finish `_get_events`. Mirrors the Cython encoder's
/// `_pack_span_events`/`pack_span_event_attributes`.
fn clone_truncate_span_event(
    py: Python<'_>,
    event: &NativeSpanEvent<PyTraceData>,
) -> NativeSpanEvent<PyTraceData> {
    NativeSpanEvent {
        time_unix_nano: event.time_unix_nano,
        name: truncate_span_text(event.name.clone_ref(py)),
        attributes: event
            .attributes
            .iter()
            .map(|(k, v)| {
                (
                    truncate_span_text(k.clone_ref(py)),
                    truncate_attribute_any_value(v.clone()),
                )
            })
            .collect(),
    }
}

/// Wire keys for the v0.5 JSON-encoded-into-meta fallback (mirror `SPAN_LINKS_KEY`/
/// `SPAN_EVENTS_KEY` in `ddtrace/internal/constants.py`).
const SPAN_LINKS_KEY: &str = "_dd.span_links";
const SPAN_EVENTS_KEY: &str = "events";

/// Build the same dict shape as the public `SpanLink.to_dict()` (see `span_link.rs`) from an
/// already-flattened `NativeSpanLink`, for JSON-encoding under the v0.5 compatibility shim.
fn span_link_to_json_dict(
    py: Python<'_>,
    link: &NativeSpanLink<PyTraceData>,
) -> PyResult<Py<PyDict>> {
    let full_trace_id = ((link.trace_id_high as u128) << 64) | (link.trace_id as u128);
    // Fields are truncated inline here (rather than pre-truncating a whole cloned
    // NativeSpanLink first) since this dict is built straight from the live `self.span_links`
    // when JSON-encoding for v0.5 — see SpanData::build_v04_span.
    let d = PyDict::new(py);
    d.set_item("trace_id", format!("{:032x}", full_trace_id))?;
    d.set_item("span_id", format!("{:016x}", link.span_id))?;
    if !link.attributes.is_empty() {
        let attrs = PyDict::new(py);
        for (k, v) in &link.attributes {
            attrs.set_item(
                &truncate_span_text(k.clone_ref(py)),
                &truncate_span_text(v.clone_ref(py)),
            )?;
        }
        d.set_item("attributes", attrs)?;
    }
    if !link.tracestate.is_empty() {
        d.set_item(
            "tracestate",
            &truncate_span_text(link.tracestate.clone_ref(py)),
        )?;
    }
    // Bit 31 encodes "flags present" (see build_native_link); unmask before surfacing,
    // matching native_span_link_to_py's reverse mapping used by `_get_links()`.
    if link.flags & 0x8000_0000 != 0 {
        d.set_item("flags", (link.flags & 0x7FFF_FFFF) as i64)?;
    }
    Ok(d.unbind())
}

/// Build the same dict shape as `dict(SpanEvent(...))` (see `SpanEvent::__iter__` in
/// `span_event.rs`) from a `NativeSpanEvent`, for JSON-encoding under the v0.5 shim. Fields are
/// truncated inline for the same reason as `span_link_to_json_dict` above.
fn span_event_to_json_dict(
    py: Python<'_>,
    event: &NativeSpanEvent<PyTraceData>,
) -> PyResult<Py<PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", &truncate_span_text(event.name.clone_ref(py)))?;
    d.set_item("time_unix_nano", event.time_unix_nano)?;
    if !event.attributes.is_empty() {
        let attrs = PyDict::new(py);
        for (k, v) in &event.attributes {
            let truncated = truncate_attribute_any_value(v.clone());
            attrs.set_item(
                &truncate_span_text(k.clone_ref(py)),
                attribute_any_value_to_py(py, &truncated)?,
            )?;
        }
        d.set_item("attributes", attrs)?;
    }
    Ok(d.unbind())
}

// Cached once on first use — `json.dumps`. Avoids a sys.modules lookup + getattr on every
// call (this is on the v0.5 default-output hot path for any span with links/events).
static JSON_DUMPS: OnceLock<Py<PyAny>> = OnceLock::new();

fn get_json_dumps(py: Python<'_>) -> PyResult<&'static Py<PyAny>> {
    get_or_init!(JSON_DUMPS, py, {
        py.import("json")
            .and_then(|m| m.getattr("dumps"))
            .map(|a| a.unbind())
    })
}

/// `json.dumps([...])` over a sequence of dicts, matching the Cython encoder's
/// `json_dumps([link.to_dict() for link in ...])` / `json_dumps([dict(e) for e in ...])`.
fn json_dumps_list<I>(py: Python<'_>, dicts: I) -> PyResult<PyBackedString>
where
    I: Iterator<Item = PyResult<Py<PyDict>>>,
{
    let list = PyList::empty(py);
    for d in dicts {
        list.append(d?)?;
    }
    get_json_dumps(py)?
        .call1(py, (list,))?
        .bind(py)
        .extract::<PyBackedString>()
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
            // Initialize span_id from parameter or generate random
            span_id: span_id
                .and_then(|obj| obj.extract::<u64>().ok())
                .unwrap_or_else(crate::rand::rand64bits),
            span_type: span_type
                .map(|obj| extract_backed_string_or_none(obj))
                .unwrap_or_else(|| PyBackedString::py_none(py)),
            // Initialize parent_id: None or invalid → 0 (no parent), Some(int) → parent_id
            parent_id: parent_id
                .and_then(|obj| obj.extract::<u64>().ok())
                .unwrap_or(0),
            // Handle start parameter: None means capture current time, otherwise convert seconds to nanoseconds
            start: match start {
                None => wall_clock_ns(), // Common case: native time capture
                Some(obj) => {
                    // start is in seconds (float or int), convert to nanoseconds
                    obj.extract::<f64>()
                        .map(|s| (s * 1e9) as i64)
                        .or_else(|_| obj.extract::<i64>().map(|s| s * 1_000_000_000))
                        .unwrap_or_else(|_| wall_clock_ns()) // Invalid value: fall back to current time
                }
            },
            // Initialize span_api: use provided value or default to "datadog"
            span_api: span_api
                .map(|obj| extract_backed_string_or_default(obj))
                .unwrap_or_else(|| PyBackedString::from_static_str("datadog")),
            // meta/metrics stay at their empty Default: a span that sets no meta (or no metrics)
            // never allocates that Vec; the first set_tag/set_metric acquires a backing buffer from
            // the thread-local recycle pool (see insert_meta/insert_metric).
            ..Default::default()
        };
        span.set_name(name);
        match service {
            Some(obj) => span.set_service(obj),
            // Directly set py_none to avoid creating a bound None and going through extraction
            None => span.service = PyBackedString::py_none(py),
        }
        // Set resource to the provided value, or default to name if None
        // Use clone_ref for efficient refcount increment with Python token
        match resource {
            Some(obj) => span.set_resource(obj),
            None => span.resource = span.name.clone_ref(py),
        }
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
        self.name.as_py(py)
    }

    #[setter]
    #[inline(always)]
    fn set_name(&mut self, name: &Bound<'_, PyAny>) {
        self.name = extract_backed_string_or_default(name);
    }

    #[getter]
    #[inline(always)]
    fn get_service<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyAny>> {
        // Return None for Python None, otherwise return the string (stored or interned)
        if self.service.is_py_none(py) {
            None
        } else {
            Some(self.service.as_py(py))
        }
    }

    #[setter]
    #[inline(always)]
    fn set_service(&mut self, service: &Bound<'_, PyAny>) {
        self.service = extract_backed_string_or_none(service);
    }

    #[getter]
    #[inline(always)]
    fn get_resource<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        // Use as_py to handle both stored (zero-copy) and static (interned) strings
        self.resource.as_py(py)
    }

    #[setter]
    #[inline(always)]
    fn set_resource(&mut self, resource: &Bound<'_, PyAny>) {
        self.resource = extract_backed_string_or_default(resource);
    }

    #[getter]
    #[inline(always)]
    fn get_span_type<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyAny>> {
        if self.span_type.is_py_none(py) {
            None
        } else {
            Some(self.span_type.as_py(py))
        }
    }

    #[setter]
    #[inline(always)]
    fn set_span_type(&mut self, span_type: &Bound<'_, PyAny>) {
        self.span_type = extract_backed_string_or_none(span_type);
    }

    // start_ns property (maps to self.start)
    #[getter]
    #[inline(always)]
    fn get_start_ns(&self) -> i64 {
        self.start
    }

    #[setter]
    #[inline(always)]
    fn set_start_ns(&mut self, value: &Bound<'_, PyAny>) {
        self.start = extract_i64_or_default(value);
    }

    // duration_ns property (maps to self.duration)
    // Returns None if duration is not set, else returns the value
    #[getter]
    #[inline(always)]
    fn get_duration_ns(&self) -> Option<i64> {
        self.duration
    }

    #[setter]
    #[inline(always)]
    fn set_duration_ns(&mut self, value: Option<&Bound<'_, PyAny>>) {
        self.duration = value.and_then(|obj| {
            obj.extract::<i64>()
                .or_else(|_| obj.extract::<f64>().map(|f| f as i64))
                .ok()
        });
    }

    // error property
    #[getter]
    #[inline(always)]
    fn get_error(&self) -> i32 {
        self.error
    }

    #[setter]
    #[inline(always)]
    fn set_error(&mut self, value: &Bound<'_, PyAny>) {
        self.error = extract_i32_or_default(value);
    }

    // span_id property
    #[getter]
    #[inline(always)]
    fn get_span_id(&self) -> u64 {
        self.span_id
    }

    #[setter]
    #[inline(always)]
    fn set_span_id(&mut self, value: &Bound<'_, PyAny>) {
        // Extract u64, silently ignore invalid types (keep existing value)
        if let Ok(id) = value.extract::<u64>() {
            self.span_id = id;
        }
    }

    // trace_id property - returns the stored trace_id as-is
    #[getter]
    #[inline(always)]
    fn get_trace_id<'py>(&mut self, py: Python<'py>) -> Bound<'py, PyAny> {
        // Lazy-init: create the Python int on first read, reuse on subsequent reads.
        // Invalidated (set to None) on every write to trace_id.
        // trace_id is always the source of truth; _trace_id_py is purely a Python-side cache.
        if self._trace_id_py.is_none() {
            let val = self.trace_id;
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
        (self.trace_id & 0xFFFF_FFFF_FFFF_FFFF) as u64
    }

    // finished property (native for performance - avoids Python property hop)
    #[getter]
    #[inline(always)]
    fn get_finished(&self) -> bool {
        self.duration.is_some()
    }

    // start property - converts start (nanoseconds) to seconds
    #[getter]
    #[inline(always)]
    fn get_start(&self) -> f64 {
        self.start as f64 / 1e9
    }

    #[setter]
    #[inline(always)]
    fn set_start(&mut self, value: &Bound<'_, PyAny>) {
        // Convert seconds to nanoseconds
        self.start = value
            .extract::<f64>()
            .map(|s| (s * 1e9) as i64)
            .or_else(|_| value.extract::<i64>().map(|s| s * 1_000_000_000))
            .unwrap_or(0);
    }

    // duration property - converts duration (nanoseconds) to seconds
    // Returns None if duration is not set, else returns seconds as f64
    #[getter]
    #[inline(always)]
    fn get_duration(&self) -> Option<f64> {
        self.duration.map(|d| d as f64 / 1e9)
    }

    #[setter]
    #[inline(always)]
    fn set_duration(&mut self, value: &Bound<'_, PyAny>) {
        // Convert seconds to nanoseconds
        self.duration = value
            .extract::<f64>()
            .map(|s| (s * 1e9) as i64)
            .or_else(|_| value.extract::<i64>().map(|s| s * 1_000_000_000))
            .ok();
    }

    // parent_id property
    // Returns None if parent_id is 0 (no parent), else returns the value
    #[getter]
    #[inline(always)]
    fn get_parent_id(&self) -> Option<u64> {
        if self.parent_id == 0 {
            None
        } else {
            Some(self.parent_id)
        }
    }

    #[setter]
    #[inline(always)]
    fn set_parent_id(&mut self, value: Option<&Bound<'_, PyAny>>) {
        self.parent_id = match value {
            None => 0,
            Some(obj) => obj.extract::<u64>().unwrap_or(self.parent_id),
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
        // Non-UTF8 keys (lone surrogates) are dropped rather than stored under a collapsed ""
        // key — see AttrKey's doc comment.
        let Ok(key_backed) = PyBackedString::try_from(key_str.clone()) else {
            return Ok(());
        };
        let attr_key = AttrKey::new(key_backed);

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
            self.insert_meta(attr_key, s.unbind());
            return Ok(());
        }

        // str → meta
        if let Ok(s) = value.cast::<PyString>() {
            self.insert_meta(attr_key, s.clone().unbind());
            return Ok(());
        }

        // float → metrics (drop NaN/Inf)
        // Check before int because some types (e.g. numpy.float64) implement __float__
        // but not __index__, so PyFloat succeeds and PyInt would fail.
        if let Ok(f) = value.cast::<PyFloat>() {
            let n = f.value();
            if n.is_nan() || n.is_infinite() {
                return Ok(());
            }
            self.insert_metric(attr_key, MetricValue::Float(n));
            return Ok(());
        }

        // int (catches bool and numpy.int* via __index__) → metrics.
        // extract::<i64>() succeeds for bool (True → 1, False → 0) and for any
        // type implementing __index__. Python ints that overflow i64 fall through
        // to the str() fallback below.
        if let Ok(n) = value.extract::<i64>() {
            self.insert_metric(attr_key, MetricValue::Int(n));
            return Ok(());
        }

        // bytes → UTF-8 decoded meta (with U+FFFD replacements for invalid sequences)
        if let Ok(b) = value.cast::<PyBytes>() {
            let decoded = String::from_utf8_lossy(b.as_bytes());
            let py_str = PyString::new(key.py(), &decoded);
            self.insert_meta(attr_key, py_str.unbind());
            return Ok(());
        }

        // Fallback: str(value) — covers Python ints that overflow i64, arbitrary objects, etc.
        let Ok(s) = value.str() else {
            return Ok(());
        };
        self.insert_meta(attr_key, s.unbind());
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

    /// Return True if the span has an attribute with the given key (in either meta or metrics).
    #[pyo3(name = "_has_attribute")]
    fn has_attribute(&self, key: &Bound<'_, PyAny>) -> bool {
        let Ok(k) = key.cast::<PyString>() else {
            return false;
        };
        let Ok(k_str) = k.to_str() else {
            return false;
        };
        self.meta.contains_key(k_str) || self.metrics.contains_key(k_str)
    }

    /// Remove an attribute by key from whichever map holds it.
    #[pyo3(name = "_remove_attribute")]
    fn remove_attribute(&mut self, key: &Bound<'_, PyAny>) {
        let Ok(k) = key.cast::<PyString>() else {
            return;
        };
        let Ok(k_str) = k.to_str() else {
            return;
        };
        // meta/metrics are mutually exclusive, so a key found in one can't be in the other;
        // only fall through to metrics when meta didn't have it.
        if self.meta.remove(k_str).is_none() {
            self.metrics.remove(k_str);
        }
    }

    /// Return the raw stored value for the given key, or None if not found.
    /// Returns the natural Python type: str for meta, int/float for metrics.
    #[pyo3(name = "_get_attribute")]
    fn get_attribute<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        self.get_str_attribute(py, key)
            .or_else(|| self.get_numeric_attribute(py, key))
    }

    /// Return the string attribute for the given key, or None if not present in `meta`.
    #[pyo3(name = "_get_str_attribute")]
    fn get_str_attribute<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let k = key.cast::<PyString>().ok()?;
        let k_str = k.to_str().ok()?;
        Some(self.meta.get(k_str)?.bind(py).clone().into_any())
    }

    /// Return the numeric attribute for the given key, or None if not present in `metrics`.
    /// Returns int for Int values and float for Float values, preserving the original type.
    #[pyo3(name = "_get_numeric_attribute")]
    fn get_numeric_attribute<'py>(
        &self,
        py: Python<'py>,
        key: &Bound<'_, PyAny>,
    ) -> Option<Bound<'py, PyAny>> {
        let k = key.cast::<PyString>().ok()?;
        let k_str = k.to_str().ok()?;
        Some(self.metrics.get(k_str)?.as_py(py))
    }

    /// Return all attributes merged into a single dict.
    /// Values are the natural Python type (str, int, or float).
    /// Used by callers that propagate span attributes (e.g. parent-span copy).
    #[pyo3(name = "_get_attributes")]
    fn get_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in self.meta.iter() {
            d.set_item(k.as_bound(py), v.bind(py))?;
        }
        for (k, v) in self.metrics.iter() {
            d.set_item(k.as_bound(py), v.as_py(py))?;
        }
        Ok(d)
    }

    /// Return the `meta` map as a Python dict snapshot.
    /// Used by the Python encoder to build the v0.4 `meta` dict.
    #[pyo3(name = "_get_str_attributes")]
    fn get_str_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in self.meta.iter() {
            d.set_item(k.as_bound(py), v.bind(py))?;
        }
        Ok(d)
    }

    /// Return the `metrics` map as a Python dict snapshot.
    /// Int values are returned as Python int; Float values as Python float.
    /// Used by the Python encoder to build the v0.4 `metrics` dict.
    #[pyo3(name = "_get_numeric_attributes")]
    fn get_numeric_attributes<'py>(&self, py: Python<'py>) -> pyo3::PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in self.metrics.iter() {
            match v {
                MetricValue::Int(i) => {
                    d.set_item(k.as_bound(py), *i)?;
                }
                MetricValue::Float(f) => {
                    d.set_item(k.as_bound(py), *f)?;
                }
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
            self.span_links.push(native_link);
        } else {
            match self.span_links.iter().position(|l| l.span_id == span_id) {
                Some(idx) => self.span_links[idx] = native_link,
                None => self.span_links.push(native_link),
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
        self.span_events.push(NativeSpanEvent {
            name,
            time_unix_nano,
            attributes: attrs,
        });
        Ok(())
    }

    /// Materialize all stored links back to PyO3 SpanLink objects.
    fn _get_links(&self, py: Python<'_>) -> PyResult<Vec<Py<SpanLink>>> {
        self.span_links
            .iter()
            .map(|l| native_span_link_to_py(py, l))
            .collect()
    }

    /// Materialize all stored events back to PyO3 SpanEvent objects.
    fn _get_events(&self, py: Python<'_>) -> PyResult<Vec<Py<SpanEvent>>> {
        self.span_events
            .iter()
            .map(|e| native_span_event_to_py(py, e))
            .collect()
    }

    fn _has_links(&self) -> bool {
        !self.span_links.is_empty()
    }

    fn _has_events(&self) -> bool {
        !self.span_events.is_empty()
    }

    // --- Cyclic GC support ---
    //
    // Without these, any reference cycle that passes through `meta_struct`
    // (`Py<PyDict>`) or `_trace_id_py` (`Py<PyAny>`) is invisible to CPython's
    // cyclic GC and leaks forever. This was the root cause of a memory
    // regression in 4.x where pure-Python span attributes were migrated to
    // native storage (libdatadog `Span<PyTraceData>`) without preserving the
    // implicit GC traversal that `__slots__` on the Python `Span` class used
    // to provide. See repro: a span -> meta_struct -> dict -> list -> span
    // cycle is uncollectable in 4.x but collectable in 3.x.
    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        if let Some(o) = &self._trace_id_py {
            visit.call(o)?;
        }
        if let Some(d) = &self.meta_struct {
            visit.call(d)?;
        }
        // PyBackedString fields hold `Py<PyAny>` storage for str/bytes/None.
        // Atomic types can't form cycles, but visit them for correct refcount
        // accounting.
        self.span_api.traverse(&visit)?;
        self.service.traverse(&visit)?;
        self.name.traverse(&visit)?;
        self.resource.traverse(&visit)?;
        self.span_type.traverse(&visit)?;
        // `self.meta` keys hold `Py<PyString>` (via AttrKey) and values hold `Py<PyString>`.
        for (k, v) in self.meta.iter() {
            k.traverse(&visit)?;
            visit.call(v)?;
        }
        // `self.metrics` values (Int/Float) are primitive Rust types with no Python
        // references; only the keys need visiting.
        for k in self.metrics.keys() {
            k.traverse(&visit)?;
        }
        for link in self.span_links.iter() {
            link.tracestate.traverse(&visit)?;
            for (k, v) in link.attributes.iter() {
                k.traverse(&visit)?;
                v.traverse(&visit)?;
            }
        }
        for event in self.span_events.iter() {
            event.name.traverse(&visit)?;
            for (k, v) in event.attributes.iter() {
                k.traverse(&visit)?;
                // AttributeAnyValue can wrap an `AttributeArrayValue::String`
                // (or an Array of them) whose `PyBackedString` storage holds a
                // `Py<PyAny>` — typically a `PyString`, but possibly a `str`
                // subclass with a `__dict__` that closes a cycle back to the
                // span. Recurse so the GC sees the SpanData -> attribute-value
                // edge.
                traverse_attr_any(v, &visit)?;
            }
        }
        Ok(())
    }

    fn __clear__(&mut self) {
        // Reset to Default to drop every owned Python reference so CPython can break
        // cycles. Assigning the whole struct is correct-by-construction: it cannot drift
        // out of sync with __traverse__ when fields are added. `Default` is a valid state
        // (duration -> None = "not finished"); the object is garbage being collected, so resetting
        // scalars and recycling collection buffers (via each field's Drop) here is harmless.
        *self = Self::default();
    }
}

// --- Cyclic GC helpers ---

/// Traverse the Python references reachable through an
/// `AttributeAnyValue<PyTraceData>` (used by span-event attribute values).
fn traverse_attr_any(
    val: &AttributeAnyValue<PyTraceData>,
    visit: &pyo3::PyVisit<'_>,
) -> Result<(), pyo3::PyTraverseError> {
    match val {
        AttributeAnyValue::SingleValue(v) => traverse_attr_array(v, visit),
        AttributeAnyValue::Array(items) => {
            for item in items {
                traverse_attr_array(item, visit)?;
            }
            Ok(())
        }
    }
}

/// Traverse the Python references reachable through an
/// `AttributeArrayValue<PyTraceData>`. Only the `String` variant carries a
/// `PyBackedString`; the rest are primitive Rust types.
fn traverse_attr_array(
    val: &AttributeArrayValue<PyTraceData>,
    visit: &pyo3::PyVisit<'_>,
) -> Result<(), pyo3::PyTraverseError> {
    if let AttributeArrayValue::String(s) = val {
        s.traverse(visit)?;
    }
    Ok(())
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
