//! Projection of a [`SpanData`] into the libdatadog v0.4 wire span.
//!
//! Every string in the wire span is a [`PyBackedString`]. Each one costs a refcount bump, not a copy,
//! and it keeps the Python `str` object alive. A holder of the wire span can therefore read every
//! field without the GIL, which is the property that lets libdatadog serialize and send detached.

use pyo3::types::{
    PyAnyMethods as _, PyBytes, PyBytesMethods as _, PyDictMethods as _, PyListMethods as _,
    PyString,
};
use pyo3::{Bound, Py, PyAny, Python};
use std::collections::HashMap;

use libdd_trace_utils::span::v04::{
    AttributeAnyValue, AttributeArrayValue, Span, SpanEvent, SpanLink,
};
use libdd_trace_utils::span::SpanText as _;

use super::attributes::AttributeValue;
use super::SpanData;
use crate::py_string::{Bytes, PyBackedString, PyTraceData};

/// Wire key for the trace-level origin tag. Mirrors `_ORIGIN_KEY` in
/// `ddtrace/internal/constants.py`.
const ORIGIN_KEY: &str = "_dd.origin";

/// Mirror `MAX_SPAN_META_VALUE_LEN` and `TRUNCATED_SPAN_ATTRIBUTE_LEN` in
/// `ddtrace/_trace/_limits.py`. This module caps every oversized user-tag field to keep wire-format
/// parity, and exempts the reserved tags that [`build_wire_span`] injects. Without the cap a payload
/// grows without bound, and downstream consumers assume the cap holds.
const MAX_SPAN_META_VALUE_LEN: usize = 25000;
const TRUNCATED_SPAN_ATTRIBUTE_LEN: usize = 2500;
const TRUNCATED_SUFFIX: &str = "<truncated>...";

/// The largest integer that an f64 represents exactly. Above this value, `as f64` rounds to the
/// nearest representable neighbour.
const MAX_EXACT_F64_INT: u64 = 1 << 53;

/// Return `s` unchanged below MAX_SPAN_META_VALUE_LEN characters, and otherwise cut to
/// TRUNCATED_SPAN_ATTRIBUTE_LEN characters. Both limits count characters, not bytes.
///
/// The check reads byte length first. A UTF-8 encoding is never shorter than its character count, so
/// a string inside the byte budget is inside the character budget too, and the common short ASCII
/// case skips the `chars().count()` scan. `s` comes by value, so that case returns it straight back
/// with no extra refcount traffic.
///
/// The oversized branch allocates a `PyString` to hold text that is already Rust-owned. Holding it
/// as a Rust string instead needs a third state on `PyBackedString::storage`, which every reader of
/// every span field would then have to discriminate. Truncation is rare, so the allocation stays.
fn wire_str(py: Python<'_>, s: PyBackedString) -> PyBackedString {
    if s.len() <= MAX_SPAN_META_VALUE_LEN || s.chars().count() <= MAX_SPAN_META_VALUE_LEN {
        return s;
    }
    let keep = TRUNCATED_SPAN_ATTRIBUTE_LEN - TRUNCATED_SUFFIX.len();
    let truncated: String = s
        .chars()
        .take(keep)
        .chain(TRUNCATED_SUFFIX.chars())
        .collect();
    PyBackedString::try_from(PyString::new(py, &truncated))
        .expect("a newly created PyString is valid UTF-8")
}

/// Cached once on first use: `ddtrace.internal._encoding.packb`.
static PACKB: std::sync::OnceLock<Py<PyAny>> = std::sync::OnceLock::new();

fn get_packb(py: Python<'_>) -> Option<&'static Py<PyAny>> {
    // OnceLock::get_or_try_init is unstable, so use get-then-set. A lost race just means two imports
    // of the same already-imported module, and `set` keeps the first winner.
    if let Some(f) = PACKB.get() {
        return Some(f);
    }
    let f = py
        .import("ddtrace.internal._encoding")
        .and_then(|m| m.getattr("packb"))
        .map(|a| a.unbind())
        .ok()?;
    let _ = PACKB.set(f);
    PACKB.get()
}

/// Log the first dropped meta_struct entry of the process.
///
/// The packer fails on the shape of a value, not on one instance of it, so the same drop repeats for
/// every span that AppSec, IAST or LLMObs tags. One line per span would flood the log, so later drops
/// stay silent.
fn log_meta_struct_drop(reason: &str) {
    static LOGGED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    if !LOGGED.swap(true, std::sync::atomic::Ordering::Relaxed) {
        tracing::warn!("dropped a meta_struct entry and will not log further drops: {reason}");
    }
}

/// Pack one meta_struct value into msgpack with the existing Python packer.
///
/// Returns None when the packer is unavailable or rejects the value. The caller runs inside
/// `span.finish()` on an application thread, so an error raised from here escapes into user code.
fn pack_meta_struct_value(py: Python<'_>, obj: &Bound<'_, PyAny>) -> Option<Bytes> {
    let Some(packb) = get_packb(py) else {
        log_meta_struct_drop("ddtrace.internal._encoding.packb is unavailable");
        return None;
    };
    let packed = match packb.call1(py, (obj,)) {
        Ok(packed) => packed,
        Err(err) => {
            log_meta_struct_drop(&format!("packb raised {err}"));
            return None;
        }
    };
    match packed.bind(py).cast::<PyBytes>() {
        Ok(bytes) => Some(Bytes::from_owned_bytes(bytes.as_bytes().to_vec())),
        Err(_) => {
            log_meta_struct_drop("packb returned a non-bytes value");
            None
        }
    }
}

/// Build the v0.4 wire span for `span`.
///
/// This function reads `span` and never drains it, because reading a finished span from Python is
/// public API: `get_tag`, `_get_links`, `_get_struct_tag` and `__repr__` all still work after a span
/// reaches the writer.
///
/// `dd_origin` is the trace-level origin from `trace[0].context.dd_origin`. It is injected as
/// `_dd.origin` into every span, because the attribute store only carries it on the chunk-root span.
/// It is not truncated: it is an internal reserved tag, exempt from the user-tag length cap.
///
/// [`wire_link`].
///
/// This function never raises. It skips a key or value with no valid UTF-8 form, and a meta_struct
/// value that the packer rejects. One lost attribute costs less than one lost span.
pub(crate) fn build_wire_span(
    py: Python<'_>,
    span: &SpanData,
    dd_origin: Option<&PyBackedString>,
) -> Span<PyTraceData> {
    let mut out = Span::<PyTraceData> {
        trace_id: span.trace_id,
        span_id: span.span_id,
        parent_id: span.parent_id,
        start: span.start,
        // `duration` is None only for an unfinished span, which the writer never receives. -1 is a
        // defensive sentinel, because the wire field is a non-optional i64 with no "unset" value.
        duration: span.duration.unwrap_or(-1),
        error: span.error,
        name: wire_str(py, span.name.clone_ref(py)),
        service: wire_str(py, span.service.clone_ref(py)),
        resource: wire_str(py, span.resource.clone_ref(py)),
        r#type: wire_str(py, span.span_type.clone_ref(py)),
        ..Default::default()
    };

    // The value variant picks the wire map: Str goes to meta, Float to metrics, and Int to whichever
    // of the two holds it exactly.
    for (key, value) in &span.attributes {
        // A key carrying lone surrogates has no valid UTF-8 wire form. Skip it rather than emit bytes
        // that the msgpack payload cannot legally carry.
        let Ok(wire_key) = PyBackedString::try_from(key.as_bound(py)) else {
            continue;
        };
        // Skip a user tag that collides with the injected reserved key below. The injected value
        // wins, and the wire keys stay unique for `mark_deduped`.
        let collides = dd_origin.is_some() && wire_key.as_ref() == ORIGIN_KEY;
        match value {
            AttributeValue::Str(s) => {
                if collides {
                    continue;
                }
                let Ok(wire_value) = PyBackedString::try_from(s.bind(py).clone()) else {
                    continue;
                };
                out.meta
                    .insert(wire_str(py, wire_key), wire_str(py, wire_value));
            }
            AttributeValue::Int(i) => {
                // The wire metrics map is f64-only, so an integer past f64's exact range would round.
                // Send those to meta as an exact decimal string instead.
                if i.unsigned_abs() <= MAX_EXACT_F64_INT {
                    out.metrics.insert(wire_str(py, wire_key), *i as f64);
                } else if !collides {
                    if let Ok(wire_value) =
                        PyBackedString::try_from(PyString::new(py, &i.to_string()))
                    {
                        out.meta
                            .insert(wire_str(py, wire_key), wire_str(py, wire_value));
                    }
                }
            }
            AttributeValue::Float(f) => {
                out.metrics.insert(wire_str(py, wire_key), *f);
            }
        }
    }

    out.span_links = span.span_links.iter().map(|l| wire_link(py, l)).collect();
    out.span_events = span.span_events.iter().map(|e| wire_event(py, e)).collect();

    if let Some(origin) = dd_origin {
        out.meta.insert(
            PyBackedString::from_static_str(ORIGIN_KEY),
            origin.clone_ref(py),
        );
    }

    // meta_struct: `items()` snapshots the dict first. The packer calls back into Python, and a
    // callback that mutates the dict during a live `PyDict_Next` makes PyO3 panic, which reaches
    // Python as a PanicException that `except Exception` does not catch.
    if let Some(meta_struct) = &span.meta_struct {
        for item in meta_struct.bind(py).items().iter() {
            let Ok((k, v)) = item.extract::<(Bound<'_, PyAny>, Bound<'_, PyAny>)>() else {
                continue;
            };
            let Ok(key) = k.cast::<PyString>() else {
                continue;
            };
            let Ok(wire_key) = PyBackedString::try_from(key.clone()) else {
                continue;
            };
            let Some(packed) = pack_meta_struct_value(py, &v) else {
                continue;
            };
            out.meta_struct.insert(wire_str(py, wire_key), packed);
        }
    }

    // Certify each wire map as deduped, so the exporter's `dedup()` is a no-op and drops its per-map
    // HashSet. `attributes` is a map, so one key holds one value and the variant picks exactly one
    // wire map. The reserved-key skips above keep user tags off the injected key. meta_struct keys
    // come from a Python dict.
    //
    // One accepted exception: truncation is not injective, so two keys longer than
    // MAX_SPAN_META_VALUE_LEN that differ only past the cut point collapse to one key, and the map
    // then carries it twice. A 25000-character tag key is already pathological, so that case does not
    // justify a dedup scan of every span.
    out.meta.mark_deduped();
    out.metrics.mark_deduped();
    out.meta_struct.mark_deduped();

    out
}

/// Project one span link, truncating every string field.
///
/// `build_native_link` sets [`SPAN_LINK_FLAGS_PRESENT`] and `_get_links` strips it again.
///
/// The v0.4 msgpack payload wants the bit set, because the agent makes the same distinction, and the
/// Cython encoder set it too (`_encoding.pyx`). Pass `mask_flags = true` for a consumer that reads
/// `flags` as a plain W3C value and does not know the convention. libdatadog's v0.4 to v0.5 JSON
/// conversion is one such consumer: it copies `flags` into `meta["_dd.span_links"]` without
/// stripping the bit, so an unmasked value reaches the agent as `0x80000001`.
fn wire_link(py: Python<'_>, link: &SpanLink<PyTraceData>) -> SpanLink<PyTraceData> {
    SpanLink {
        trace_id: link.trace_id,
        trace_id_high: link.trace_id_high,
        span_id: link.span_id,
        tracestate: wire_str(py, link.tracestate.clone_ref(py)),
        flags: link.flags,
        attributes: link
            .attributes
            .iter()
            .map(|(k, v)| (wire_str(py, k.clone_ref(py)), wire_str(py, v.clone_ref(py))))
            .collect::<HashMap<_, _>>(),
    }
}

/// Project one span event, truncating every string field.
fn wire_event(py: Python<'_>, event: &SpanEvent<PyTraceData>) -> SpanEvent<PyTraceData> {
    SpanEvent {
        time_unix_nano: event.time_unix_nano,
        name: wire_str(py, event.name.clone_ref(py)),
        attributes: event
            .attributes
            .iter()
            .map(|(k, v)| (wire_str(py, k.clone_ref(py)), wire_attr_any(py, v)))
            .collect::<HashMap<_, _>>(),
    }
}

fn wire_attr_any(
    py: Python<'_>,
    value: &AttributeAnyValue<PyTraceData>,
) -> AttributeAnyValue<PyTraceData> {
    match value {
        AttributeAnyValue::SingleValue(v) => AttributeAnyValue::SingleValue(wire_attr_array(py, v)),
        AttributeAnyValue::Array(items) => {
            AttributeAnyValue::Array(items.iter().map(|v| wire_attr_array(py, v)).collect())
        }
    }
}

/// Project one attribute value. Only the String variant carries text to truncate. Cloning that string
/// takes the GIL token, because the clone increments the refcount of the Python object behind it.
fn wire_attr_array(
    py: Python<'_>,
    value: &AttributeArrayValue<PyTraceData>,
) -> AttributeArrayValue<PyTraceData> {
    match value {
        AttributeArrayValue::String(s) => {
            AttributeArrayValue::String(wire_str(py, s.clone_ref(py)))
        }
        AttributeArrayValue::Boolean(b) => AttributeArrayValue::Boolean(*b),
        AttributeArrayValue::Integer(i) => AttributeArrayValue::Integer(*i),
        AttributeArrayValue::Double(d) => AttributeArrayValue::Double(*d),
    }
}
