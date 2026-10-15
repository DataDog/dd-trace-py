//! Direct Python traversal into final, owned ddwaf storage.
#[cfg(all(not(Py_LIMITED_API), not(any(PyPy, GraalPy))))]
use libddwaf::object::AsRawMutObject;
use libddwaf::object::{Keyed, WafArray, WafMap, WafObject, WafObjectType, WafString};
use pyo3::exceptions::{PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
#[cfg(all(not(Py_LIMITED_API), not(any(PyPy, GraalPy))))]
use pyo3::types::PyStringData;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
#[cfg(all(not(Py_LIMITED_API), not(any(PyPy, GraalPy))))]
use std::alloc::{alloc, handle_alloc_error, Layout};

#[derive(Clone, Copy)]
pub struct Limits {
    pub objects: usize,
    pub depth: usize,
    pub string: usize,
    pub compatibility: bool,
}

#[pyclass(
    module = "ddtrace.internal.native._native.ddwaf",
    frozen,
    get_all,
    skip_from_py_object
)]
#[derive(Clone, Default)]
pub struct Stats {
    pub string_length: Option<usize>,
    pub container_size: Option<usize>,
    pub container_depth: Option<usize>,
    pub nodes: usize,
    pub string_bytes: usize,
    pub heap_allocations: usize,
    pub container_bytes: usize,
}

impl Stats {
    pub fn python(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("string_length", self.string_length)?;
        dict.set_item("container_size", self.container_size)?;
        dict.set_item("container_depth", self.container_depth)?;
        dict.set_item("nodes", self.nodes)?;
        dict.set_item("string_bytes", self.string_bytes)?;
        dict.set_item("heap_allocations", self.heap_allocations)?;
        dict.set_item("container_bytes", self.container_bytes)?;
        Ok(dict.into_any().unbind())
    }
}

fn maximum(slot: &mut Option<usize>, value: usize) {
    *slot = Some(slot.unwrap_or(0).max(value));
}

fn bytes(value: &[u8], limits: Limits, stats: &mut Stats) -> PyResult<WafObject> {
    if value.len() > limits.string {
        maximum(&mut stats.string_length, value.len());
    }
    let value = &value[..value.len().min(limits.string)];
    stats.string_bytes += value.len();
    stats.heap_allocations += usize::from(value.len() > 14);
    WafString::new(value)
        .map(Into::into)
        .ok_or_else(|| PyValueError::new_err("string exceeds libddwaf's representable size"))
}

#[cfg(all(not(Py_LIMITED_API), not(any(PyPy, GraalPy))))]
fn string(value: &Bound<'_, PyString>, limits: Limits, stats: &mut Stats) -> PyResult<WafObject> {
    // SAFETY: attached CPython, immutable PyUnicode, little-endian arm64/x86_64.
    // PyO3's raw Unicode layout is covered by differential checks on both OSes.
    // No borrowed Python data survives this function or crosses a detached section.
    let data = unsafe { value.data()? };
    if let PyStringData::Ucs1(data) = data {
        if data.is_ascii() {
            return bytes(data, limits, stats);
        }
    }
    // Reuse a UTF-8 cache created by another consumer, without ever creating one.
    // SAFETY: a non-ASCII CPython string has a PyCompactUnicodeObject header
    // (including noncompact strings), the PyO3 0.28.3 FFI layout is versioned,
    // and the GIL is held. Cached storage is immutable and owned by value.
    let cached = unsafe { &*value.as_ptr().cast::<pyo3::ffi::PyCompactUnicodeObject>() };
    if !cached.utf8.is_null() {
        let utf8 =
            unsafe { std::slice::from_raw_parts(cached.utf8.cast(), cached.utf8_length as usize) };
        return bytes(utf8, limits, stats);
    }
    // Read UCS storage directly. Calling to_str() here would populate a retained
    // CPython UTF-8 cache, duplicating every non-ASCII source string indefinitely.
    match data {
        PyStringData::Ucs1(data) => unicode(data.iter().copied().map(u32::from), limits, stats),
        PyStringData::Ucs2(data) => unicode(data.iter().copied().map(u32::from), limits, stats),
        PyStringData::Ucs4(data) => unicode(data.iter().copied(), limits, stats),
    }
}

// Python 3.15 currently builds with PyO3's limited-ABI workaround. That ABI
// intentionally hides Unicode storage; preserve the original encoding policy.
#[cfg(any(Py_LIMITED_API, PyPy, GraalPy))]
fn string(value: &Bound<'_, PyString>, limits: Limits, stats: &mut Stats) -> PyResult<WafObject> {
    let encoded = value.call_method1("encode", ("utf-8", "ignore"))?;
    bytes(encoded.cast::<PyBytes>()?.as_bytes(), limits, stats)
}

#[cfg(all(not(Py_LIMITED_API), not(any(PyPy, GraalPy))))]
fn unicode(
    points: impl Iterator<Item = u32> + Clone,
    limits: Limits,
    stats: &mut Stats,
) -> PyResult<WafObject> {
    // Python's UCS2 holds individual code points, not UTF-16 surrogate pairs.
    // Ignore surrogate code points exactly like str.encode(errors="ignore").
    // A branchless length calculation permits vectorization over UCS storage.
    // Valid CPython storage bounds make this sum fit usize (at most twice the
    // bytes of UCS1 storage, 1.5 times UCS2 storage, or the bytes of UCS4 storage).
    let full_len: usize = points
        .clone()
        .map(|point| {
            if (0xd800..=0xdfff).contains(&point) {
                0
            } else {
                1 + usize::from(point > 0x7f)
                    + usize::from(point > 0x7ff)
                    + usize::from(point > 0xffff)
            }
        })
        .sum();
    let chars = points.filter_map(char::from_u32);
    if full_len > limits.string {
        maximum(&mut stats.string_length, full_len);
    }
    let len = full_len.min(limits.string);
    if len <= 14 {
        let mut small = [0u8; 14];
        // SAFETY: small contains at least len writable bytes.
        unsafe {
            write_utf8(small.as_mut_ptr(), len, chars);
        }
        return bytes(&small[..len], limits, stats);
    }
    let size = u32::try_from(len)
        .map_err(|_| PyValueError::new_err("string exceeds libddwaf's representable size"))?;
    let layout = Layout::array::<u8>(len).map_err(error_layout)?;
    // Allocate exactly the final native buffer. Upstream WafString::Drop and the
    // Rust allocator supplied to context.run both deallocate with this layout.
    // This small unsafe helper is a candidate for an upstream owned-string writer API.
    let pointer = unsafe { alloc(layout) };
    if pointer.is_null() {
        handle_alloc_error(layout);
    }
    let mut result = WafString::default();
    // SAFETY: default is a heap STRING with null pointer and size 0. Keep its type;
    // replace empty storage with a Rust-owned allocation and matching length.
    // Fill all len bytes before exposing the object. No Python callback occurs.
    unsafe {
        let raw = result.as_raw_mut();
        raw.via.str_.ptr = pointer.cast();
        raw.via.str_.size = size;
        write_utf8(pointer, len, chars);
    }
    stats.string_bytes += len;
    stats.heap_allocations += 1;
    Ok(result.into())
}

#[cfg(all(not(Py_LIMITED_API), not(any(PyPy, GraalPy))))]
fn error_layout(error: std::alloc::LayoutError) -> PyErr {
    PyValueError::new_err(error.to_string())
}

/// Write precisely the first len UTF-8 bytes; truncation may split a code point.
/// The caller supplies len valid, writable bytes and the length of this encoding
/// must be at least len. No ownership or uninitialized bytes escape this helper.
#[cfg(all(not(Py_LIMITED_API), not(any(PyPy, GraalPy))))]
unsafe fn write_utf8(pointer: *mut u8, len: usize, chars: impl Iterator<Item = char>) {
    let mut written = 0;
    for character in chars {
        if written == len {
            break;
        }
        let mut scratch = [0u8; 4];
        let encoded = character.encode_utf8(&mut scratch).as_bytes();
        let take = encoded.len().min(len - written);
        unsafe {
            // Constant-size copies inline. A variable 1..4-byte memcpy here was
            // unexpectedly expensive on macOS for large UCS4 strings.
            let target = pointer.add(written);
            match take {
                1 => std::ptr::copy_nonoverlapping(encoded.as_ptr(), target, 1),
                2 => std::ptr::copy_nonoverlapping(encoded.as_ptr(), target, 2),
                3 => std::ptr::copy_nonoverlapping(encoded.as_ptr(), target, 3),
                4 => std::ptr::copy_nonoverlapping(encoded.as_ptr(), target, 4),
                _ => unreachable!(),
            }
        }
        written += take;
    }
    debug_assert_eq!(written, len);
}

fn container_limits(limits: Limits, stats: &mut Stats) -> Limits {
    if limits.depth == 0 {
        // Existing ddtrace telemetry reports its default maximum, including with custom limits.
        maximum(&mut stats.container_depth, 20);
        Limits {
            objects: 0,
            ..limits
        }
    } else {
        Limits {
            objects: limits.objects.min(65535),
            ..limits
        }
    }
}

fn array<'py>(
    len: usize,
    iter: impl IntoIterator<Item = Bound<'py, PyAny>>,
    limits: Limits,
    stats: &mut Stats,
) -> PyResult<WafObject> {
    let limits = container_limits(limits, stats);
    let count = len.min(limits.objects);
    if len > count {
        maximum(&mut stats.container_size, len);
    }
    stats.heap_allocations += usize::from(count != 0);
    stats.container_bytes += count * 16;
    let mut result = WafArray::new(count).map_err(|e| PyValueError::new_err(e.to_string()))?;
    for (slot, value) in result.iter_mut().zip(iter) {
        *slot = convert(
            &value,
            Limits {
                depth: limits.depth.saturating_sub(1),
                ..limits
            },
            stats,
        )?;
    }
    Ok(result.into())
}

fn map(value: &Bound<'_, PyDict>, limits: Limits, stats: &mut Stats) -> PyResult<WafObject> {
    let original_len = value.len();
    let limits = container_limits(limits, stats);
    let count = original_len.min(limits.objects);
    stats.heap_allocations += usize::from(count != 0);
    stats.container_bytes += count * 32;
    let mut result = WafMap::new(count).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let mut inserted = 0;
    for (key, item) in value.iter().take(original_len) {
        // Existing bindings discard non-exact str/bytes keys, including subclasses.
        if !key.is_exact_instance_of::<PyString>() && !key.is_exact_instance_of::<PyBytes>() {
            continue;
        }
        if inserted == count {
            maximum(&mut stats.container_size, original_len);
            break;
        }
        let key = if let Ok(text) = key.cast_exact::<PyString>() {
            string(text, limits, stats)?
        } else {
            bytes(key.cast_exact::<PyBytes>()?.as_bytes(), limits, stats)?
        };
        let converted = convert(
            &item,
            Limits {
                depth: limits.depth.saturating_sub(1),
                ..limits
            },
            stats,
        )?;
        if value.len() != original_len {
            return Err(PyRuntimeError::new_err(
                "dictionary changed size during WAF conversion",
            ));
        }
        result[inserted] = Keyed::new(key, converted);
        inserted += 1;
    }
    result.truncate(inserted);
    Ok(result.into())
}

fn generic_array(
    value: &Bound<'_, PyAny>,
    limits: Limits,
    stats: &mut Stats,
) -> PyResult<WafObject> {
    let len = value.len()?;
    let limits = container_limits(limits, stats);
    let count = len.min(limits.objects);
    if len > count {
        maximum(&mut stats.container_size, len);
    }
    stats.heap_allocations += usize::from(count != 0);
    stats.container_bytes += count * 16;
    let mut result = WafArray::new(count).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let mut inserted = 0;
    for value in value.try_iter()?.take(count) {
        result[inserted] = convert(
            &value?,
            Limits {
                depth: limits.depth.saturating_sub(1),
                ..limits
            },
            stats,
        )?;
        inserted += 1;
    }
    result.truncate(inserted);
    Ok(result.into())
}

fn generic_map(value: &Bound<'_, PyAny>, limits: Limits, stats: &mut Stats) -> PyResult<WafObject> {
    let original_len = value.len()?;
    let limits = container_limits(limits, stats);
    let count = original_len.min(limits.objects);
    stats.heap_allocations += usize::from(count != 0);
    stats.container_bytes += count * 32;
    let mut result = WafMap::new(count).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let mut inserted = 0;
    for pair in value.call_method0("items")?.try_iter()? {
        let pair = pair?;
        let pair = pair.cast::<PyTuple>()?;
        if pair.len() != 2 {
            return Err(PyValueError::new_err("mapping items must be pairs"));
        }
        let key = pair.get_item(0)?;
        if !key.is_exact_instance_of::<PyString>() && !key.is_exact_instance_of::<PyBytes>() {
            continue;
        }
        if inserted == count {
            maximum(&mut stats.container_size, original_len);
            break;
        }
        let key = if let Ok(text) = key.cast_exact::<PyString>() {
            string(text, limits, stats)?
        } else {
            bytes(key.cast_exact::<PyBytes>()?.as_bytes(), limits, stats)?
        };
        let value = convert(
            &pair.get_item(1)?,
            Limits {
                depth: limits.depth.saturating_sub(1),
                ..limits
            },
            stats,
        )?;
        result[inserted] = Keyed::new(key, value);
        inserted += 1;
    }
    result.truncate(inserted);
    Ok(result.into())
}

pub fn convert(value: &Bound<'_, PyAny>, limits: Limits, stats: &mut Stats) -> PyResult<WafObject> {
    stats.nodes += 1;
    if let Ok(dict) = value.cast_exact::<PyDict>() {
        return map(dict, limits, stats);
    }
    if let Ok(text) = value.cast_exact::<PyString>() {
        return string(text, limits, stats);
    }
    if let Ok(list) = value.cast_exact::<PyList>() {
        return array(list.len(), list.iter(), limits, stats);
    }
    if value.is_exact_instance_of::<PyBool>() {
        return Ok(value.extract::<bool>()?.into());
    }
    if value.is_exact_instance_of::<PyInt>() {
        // ctypes c_int64 wraps arbitrary Python integers modulo 2**64.
        // SAFETY: Python is attached and value is a real PyLong. Check the C error indicator.
        let integer = unsafe { pyo3::ffi::PyLong_AsUnsignedLongLongMask(value.as_ptr()) };
        if PyErr::occurred(value.py()) {
            return Err(PyErr::fetch(value.py()));
        }
        return Ok((integer as i64).into());
    }
    if value.is_exact_instance_of::<PyFloat>() {
        return Ok(value.extract::<f64>()?.into());
    }
    if let Ok(value) = value.cast_exact::<PyBytes>() {
        return bytes(value.as_bytes(), limits, stats);
    }
    if value.is_none() {
        return Ok(().into());
    }
    if let Ok(tuple) = value.cast_exact::<PyTuple>() {
        return array(tuple.len(), tuple.iter(), limits, stats);
    }
    if limits.compatibility {
        // Deliberate slow path; Python callbacks can mutate user-defined containers.
        let abc = value.py().import("collections.abc")?;
        if value.is_instance(&abc.getattr("Sequence")?)? {
            return generic_array(value, limits, stats);
        }
        if value.is_instance(&abc.getattr("Mapping")?)? {
            return generic_map(value, limits, stats);
        }
        return string(&value.str()?, limits, stats);
    }
    Err(PyTypeError::new_err(format!(
        "unsupported input type: {}",
        value.get_type().name()?
    )))
}

pub fn materialize(py: Python<'_>, value: &WafObject) -> PyResult<Py<PyAny>> {
    Ok(match value.object_type() {
        WafObjectType::String => {
            let bytes = value.as_type::<WafString>().unwrap().as_bytes();
            // SAFETY: input is a valid byte slice, CPython copies it, and the GIL is held.
            unsafe {
                Bound::from_owned_ptr_or_err(
                    py,
                    pyo3::ffi::PyUnicode_DecodeUTF8(
                        bytes.as_ptr().cast(),
                        bytes.len() as isize,
                        c"ignore".as_ptr(),
                    ),
                )?
            }
            .unbind()
        }
        WafObjectType::Map => {
            let result = PyDict::new(py);
            for entry in value.as_type::<WafMap>().unwrap().iter() {
                result.set_item(
                    materialize(py, entry.key())?,
                    materialize(py, entry.value())?,
                )?;
            }
            result.into_any().unbind()
        }
        WafObjectType::Array => {
            let result = PyList::empty(py);
            for entry in value.as_type::<WafArray>().unwrap().iter() {
                result.append(materialize(py, entry)?)?;
            }
            result.into_any().unbind()
        }
        WafObjectType::Signed => value
            .to_i64()
            .unwrap()
            .into_pyobject(py)?
            .into_any()
            .unbind(),
        WafObjectType::Unsigned => value
            .to_u64()
            .unwrap()
            .into_pyobject(py)?
            .into_any()
            .unbind(),
        WafObjectType::Bool => value
            .to_bool()
            .unwrap()
            .into_pyobject(py)?
            .to_owned()
            .into_any()
            .unbind(),
        WafObjectType::Float => value
            .to_f64()
            .unwrap()
            .into_pyobject(py)?
            .into_any()
            .unbind(),
        WafObjectType::Null | WafObjectType::Invalid => py.None(),
        _ => return Err(PyValueError::new_err("unknown native object type")),
    })
}

#[pymethods]
impl Stats {
    #[new]
    fn new() -> Self {
        Self::default()
    }

    fn as_dict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.python(py)
    }
}
