//! Marshalling between Python values and the high-level `libddwaf` object model.
//!
//! This replaces the previous raw `*mut ddwaf_object` slot wrapper: building a Python value produces
//! an owned [`WafObject`]/[`WafMap`] (whose lifetime and ownership are managed by the `libddwaf`
//! crate), and reading converts a [`WafObject`] back into Python objects. Nothing here touches
//! `libddwaf-sys` directly.

use libddwaf::object::{Keyed, WafArray, WafMap, WafObject, WafObjectType, WafString};
use pyo3::prelude::*;
use pyo3::types::{
    PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyMapping, PySequence, PyString,
};

// Containers store their size/capacity in a uint16, so they cannot hold more than this.
const DDWAF_OBJ_MAX_CAPACITY: usize = 0xFFFF;
// Value recorded as the "depth" truncation marker, matching the Python DDWAF_MAX_CONTAINER_DEPTH
// constant (the previous ctypes builder recorded this constant, not the actual depth).
// AIDEV-NOTE: keep in sync with DDWAF_MAX_CONTAINER_DEPTH in ddtrace/appsec/_ddwaf/ddwaf_types.py.
const DDWAF_MAX_CONTAINER_DEPTH: u64 = 20;

/// Tracks truncations performed while building a [`WafObject`], mirroring the Python `_observator`
/// (max-tracking of the largest string length / container size / nesting depth seen).
#[derive(Default, Clone, Copy)]
pub(crate) struct Truncation {
    pub string_length: Option<u64>,
    pub container_size: Option<u64>,
    pub container_depth: Option<u64>,
}

impl Truncation {
    fn set_string_length(&mut self, length: u64) {
        self.string_length = Some(self.string_length.map_or(length, |v| v.max(length)));
    }
    fn set_container_size(&mut self, size: u64) {
        self.container_size = Some(self.container_size.map_or(size, |v| v.max(size)));
    }
    fn set_container_depth(&mut self, depth: u64) {
        self.container_depth = Some(self.container_depth.map_or(depth, |v| v.max(depth)));
    }

    /// Convert to the `(string_length, container_size, container_depth)` tuple the Python side uses
    /// to rebuild an `_observator`.
    pub(crate) fn into_py<'py>(self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        Ok((
            self.string_length,
            self.container_size,
            self.container_depth,
        )
            .into_pyobject(py)?
            .into_any())
    }
}

/// Limits applied while building, matching the Python builder's parameters.
#[derive(Clone, Copy)]
pub(crate) struct Limits {
    pub max_objects: usize,
    pub max_depth: i64,
    pub max_string_length: usize,
}

/// Returns the UTF-8 bytes of a Python `str`, dropping anything that isn't valid UTF-8 (matching the
/// previous `str.encode("UTF-8", errors="ignore")`). The common (no surrogate) case is zero-copy.
fn str_bytes(value: &Bound<'_, PyString>) -> PyResult<Vec<u8>> {
    match value.to_str() {
        Ok(s) => Ok(s.as_bytes().to_vec()),
        Err(_) => value
            .call_method1("encode", ("utf-8", "ignore"))?
            .extract::<Vec<u8>>(),
    }
}

/// Truncates a byte string to `max_string_length`, recording the original length on truncation.
fn truncate_bytes<'a>(bytes: &'a [u8], limits: &Limits, trunc: &mut Truncation) -> &'a [u8] {
    if bytes.len() > limits.max_string_length {
        trunc.set_string_length(bytes.len() as u64);
        &bytes[..limits.max_string_length]
    } else {
        bytes
    }
}

fn waf_string(bytes: &[u8]) -> WafObject {
    WafString::new(bytes).map_or_else(WafObject::default, Into::into)
}

/// Effective per-container element cap for the current depth, applying the uint16 ceiling and the
/// depth limit. Returns 0 (and records the depth truncation) once the depth budget is exhausted.
fn container_cap(limits: &Limits, trunc: &mut Truncation) -> usize {
    if limits.max_depth <= 0 {
        trunc.set_container_depth(DDWAF_MAX_CONTAINER_DEPTH);
        0
    } else {
        limits.max_objects.min(DDWAF_OBJ_MAX_CAPACITY)
    }
}

/// Recursively builds a [`WafObject`] from a Python value, mirroring the previous Python builder's
/// type dispatch, limits, and truncation tracking.
pub(crate) fn build_object(
    value: &Bound<'_, PyAny>,
    limits: Limits,
    trunc: &mut Truncation,
) -> PyResult<WafObject> {
    // Order matters: bytes/bool before int (bool is an int subclass), containers last.
    if let Ok(dict) = value.cast::<PyDict>() {
        return build_map(dict.iter(), dict.len(), limits, trunc);
    }
    if let Ok(s) = value.cast::<PyString>() {
        let bytes = str_bytes(s)?;
        return Ok(waf_string(truncate_bytes(&bytes, &limits, trunc)));
    }
    if let Ok(b) = value.cast::<PyBytes>() {
        return Ok(waf_string(truncate_bytes(b.as_bytes(), &limits, trunc)));
    }
    if let Ok(b) = value.cast::<PyBool>() {
        return Ok(WafObject::from(b.is_true()));
    }
    if let Ok(i) = value.cast::<PyInt>() {
        // libddwaf stores a signed 64-bit int; mask to the low 64 bits (two's complement) like the
        // old ctypes c_int64 did, so arbitrarily large Python ints don't overflow.
        let v = unsafe { pyo3::ffi::PyLong_AsUnsignedLongLongMask(i.as_ptr()) } as i64;
        return Ok(WafObject::from(v));
    }
    if let Ok(f) = value.cast::<PyFloat>() {
        return Ok(WafObject::from(f.value()));
    }
    if value.is_none() {
        return Ok(WafObject::from(()));
    }
    if let Ok(list) = value.cast::<PyList>() {
        return build_array(list.iter(), list.len(), limits, trunc);
    }
    if let Ok(map) = value.cast::<PyMapping>() {
        // Mapping subclasses (e.g. CaseInsensitiveDict). Collect (key, value) pairs first.
        let mut pairs: Vec<(Bound<'_, PyAny>, Bound<'_, PyAny>)> = Vec::new();
        for pair in map.items()?.try_iter()? {
            let pair = pair?;
            let seq = pair.cast::<PySequence>()?;
            pairs.push((seq.get_item(0)?, seq.get_item(1)?));
        }
        let len = pairs.len();
        return build_map(pairs.into_iter(), len, limits, trunc);
    }
    if let Ok(seq) = value.cast::<PySequence>() {
        // Sequence subclasses (tuple, range, ...).
        let len = seq.len().unwrap_or(0);
        let items: Vec<Bound<'_, PyAny>> = seq.try_iter()?.collect::<PyResult<_>>()?;
        return build_array(items.into_iter(), len, limits, trunc);
    }
    // Fallback: stringify, like the Python builder's final `str(struct)` branch.
    let s = value.str()?;
    let bytes = str_bytes(&s)?;
    Ok(waf_string(truncate_bytes(&bytes, &limits, trunc)))
}

fn build_array<'py>(
    items: impl Iterator<Item = Bound<'py, PyAny>>,
    len: usize,
    limits: Limits,
    trunc: &mut Truncation,
) -> PyResult<WafObject> {
    let cap = container_cap(&limits, trunc);
    let reserve = len.min(cap);
    let mut array = WafArray::new(reserve as u16);
    let child_limits = Limits {
        max_depth: limits.max_depth - 1,
        ..limits
    };
    let mut idx = 0usize;
    for elt in items {
        if idx >= cap {
            trunc.set_container_size(len as u64);
            break;
        }
        array[idx] = build_object(&elt, child_limits, trunc)?;
        idx += 1;
    }
    array.truncate(idx as u16);
    Ok(array.into())
}

fn build_map<'py>(
    items: impl Iterator<Item = (Bound<'py, PyAny>, Bound<'py, PyAny>)>,
    len: usize,
    limits: Limits,
    trunc: &mut Truncation,
) -> PyResult<WafObject> {
    let cap = container_cap(&limits, trunc);
    let reserve = len.min(cap);
    let mut map = WafMap::new(reserve as u16);
    let child_limits = Limits {
        max_depth: limits.max_depth - 1,
        ..limits
    };
    let mut idx = 0usize;
    for (key, val) in items {
        // Only string/bytes keys are kept (others are silently discarded, as before).
        let key_bytes = if let Ok(s) = key.cast::<PyString>() {
            str_bytes(s)?
        } else if let Ok(b) = key.cast::<PyBytes>() {
            b.as_bytes().to_vec()
        } else {
            continue;
        };
        if idx >= cap {
            trunc.set_container_size(len as u64);
            break;
        }
        let key_bytes = truncate_bytes(&key_bytes, &limits, trunc);
        let key_obj = WafString::new(key_bytes).unwrap_or_default();
        let value_obj = build_object(&val, child_limits, trunc)?;
        map[idx] = Keyed::new(key_obj, value_obj);
        idx += 1;
    }
    map.truncate(idx as u16);
    Ok(map.into())
}

/// Recursively converts a [`WafObject`] into a Python value (replaces the old `.struct` reader).
pub(crate) fn read_object<'py>(py: Python<'py>, obj: &WafObject) -> PyResult<Bound<'py, PyAny>> {
    match obj.object_type() {
        WafObjectType::Bool => Ok(obj
            .to_bool()
            .unwrap_or(false)
            .into_pyobject(py)?
            .to_owned()
            .into_any()),
        WafObjectType::Signed => Ok(obj.to_i64().unwrap_or(0).into_pyobject(py)?.into_any()),
        WafObjectType::Unsigned => Ok(obj.to_u64().unwrap_or(0).into_pyobject(py)?.into_any()),
        WafObjectType::Float => Ok(obj.to_f64().unwrap_or(0.0).into_pyobject(py)?.into_any()),
        WafObjectType::String => {
            let bytes = obj
                .as_type::<WafString>()
                .map(WafString::as_bytes)
                .unwrap_or(b"");
            Ok(PyString::new(py, &String::from_utf8_lossy(bytes)).into_any())
        }
        WafObjectType::Array => {
            let list = PyList::empty(py);
            if let Some(arr) = obj.as_type::<WafArray>() {
                for elt in arr.iter() {
                    list.append(read_object(py, elt)?)?;
                }
            }
            Ok(list.into_any())
        }
        WafObjectType::Map => read_map(py, obj.as_type::<WafMap>()),
        // Invalid/Null and any future (non_exhaustive) variant map to None.
        _ => Ok(py.None().into_bound(py)),
    }
}

/// Converts a [`WafMap`] into a Python `dict` (keys read recursively, like the old reader).
pub(crate) fn read_map<'py>(py: Python<'py>, map: Option<&WafMap>) -> PyResult<Bound<'py, PyAny>> {
    let dict = PyDict::new(py);
    if let Some(map) = map {
        for kv in map.iter() {
            dict.set_item(read_object(py, kv.key())?, read_object(py, kv.value())?)?;
        }
    }
    Ok(dict.into_any())
}
