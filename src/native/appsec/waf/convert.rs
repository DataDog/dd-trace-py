//! Python <-> `ddwaf_object` conversion.
//!
//! Encoding walks Python containers once and writes straight into libddwaf storage allocated
//! with libddwaf's default allocator: each string is copied (or transcoded) exactly once, from
//! the Python buffer into its final native location. Decoding reads native results directly
//! into Python objects, without an intermediate Rust tree.
//!
//! Coercion, limits and truncation reporting mirror the ctypes serializer in
//! `ddtrace/appsec/_ddwaf/ddwaf_types.py`, which stays the differential oracle in tests.

use super::abi::*;
use pyo3::exceptions::{PyMemoryError, PyRuntimeError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyList, PyString, PyTuple};
use std::ffi::c_char;

/// Reported as the truncated depth whatever the configured limit, like the ctypes serializer.
const DDWAF_MAX_CONTAINER_DEPTH: usize = 20;

#[derive(Clone, Copy)]
pub struct Limits {
    pub objects: usize,
    pub depth: i64,
    pub string: usize,
}

impl Limits {
    pub const DEFAULT: Limits = Limits {
        objects: 256,
        depth: 20,
        string: 4096,
    };
    /// Rule configurations: `create_without_limits` in the ctypes serializer.
    pub const UNLIMITED: Limits = Limits {
        objects: 1 << 31,
        depth: 1000,
        string: 1 << 31,
    };
}

/// Largest truncated (string length, container size, container depth), like `_observator`.
#[derive(Default)]
pub struct Truncation {
    pub string_length: Option<usize>,
    pub container_size: Option<usize>,
    pub container_depth: Option<usize>,
}

pub type Truncations = (Option<usize>, Option<usize>, Option<usize>);

impl Truncation {
    pub fn into_tuple(self) -> Truncations {
        (
            self.string_length,
            self.container_size,
            self.container_depth,
        )
    }
}

#[inline]
fn bump(slot: &mut Option<usize>, value: usize) {
    *slot = Some(slot.map_or(value, |current| current.max(value)));
}

/// Destroys a native object (with the default allocator) unless released.
pub struct Owned<'a> {
    pub obj: ddwaf_object,
    api: &'a Api,
}

impl<'a> Owned<'a> {
    #[inline]
    pub fn new(api: &'a Api, obj: ddwaf_object) -> Self {
        Self { obj, api }
    }

    #[inline]
    pub fn release(self) -> ddwaf_object {
        let obj = self.obj;
        std::mem::forget(self);
        obj
    }
}

impl Drop for Owned<'_> {
    fn drop(&mut self) {
        // SAFETY: every object reaching an `Owned` was built with the default allocator and is
        // fully initialized (containers only count initialized slots).
        unsafe { (self.api.ddwaf_object_destroy)(&mut self.obj, self.api.default_allocator) }
    }
}

static ABC: PyOnceLock<(Py<PyAny>, Py<PyAny>)> = PyOnceLock::new();

fn abc_types(py: Python<'_>) -> PyResult<&'static (Py<PyAny>, Py<PyAny>)> {
    ABC.get_or_try_init(py, || {
        let abc = py.import("collections.abc")?;
        Ok((
            abc.getattr("Sequence")?.unbind(),
            abc.getattr("Mapping")?.unbind(),
        ))
    })
}

#[inline(always)]
fn utf8_len(point: u32) -> usize {
    // Lone surrogates are dropped, like str.encode("utf-8", "ignore").
    if (0xD800..=0xDFFF).contains(&point) {
        0
    } else {
        1 + usize::from(point > 0x7F) + usize::from(point > 0x7FF) + usize::from(point > 0xFFFF)
    }
}

/// Writes exactly `out.len()` bytes of the UTF-8 encoding; truncation may split a code point,
/// matching the byte-based `encoded[:limit]` slice of the ctypes serializer.
#[inline]
fn write_utf8(out: &mut [u8], points: impl Iterator<Item = u32>) {
    let mut written = 0;
    for character in points.filter_map(char::from_u32) {
        if written == out.len() {
            break;
        }
        let mut scratch = [0u8; 4];
        let encoded = character.encode_utf8(&mut scratch).as_bytes();
        let take = encoded.len().min(out.len() - written);
        out[written..written + take].copy_from_slice(&encoded[..take]);
        written += take;
    }
    debug_assert_eq!(written, out.len());
}

pub struct Encoder<'a, 'py> {
    api: &'a Api,
    py: Python<'py>,
    limits: Limits,
    pub truncation: Truncation,
}

impl<'a, 'py> Encoder<'a, 'py> {
    pub fn new(api: &'a Api, py: Python<'py>, limits: Limits) -> Self {
        Self {
            api,
            py,
            limits,
            truncation: Truncation::default(),
        }
    }

    /// On success the caller owns the returned object; on error nothing is leaked.
    pub fn encode(&mut self, value: &Bound<'py, PyAny>) -> PyResult<ddwaf_object> {
        self.value(value, self.limits.depth)
    }

    fn value(&mut self, value: &Bound<'py, PyAny>, depth: i64) -> PyResult<ddwaf_object> {
        let ptr = value.as_ptr();
        // Exact-type dispatch, ordered by frequency, mirrors `_build_ddwaf_object`.
        // SAFETY: attached to the interpreter; each cast follows its exact type check.
        unsafe {
            if ffi::PyDict_CheckExact(ptr) != 0 {
                return self.dict(value.cast_unchecked::<PyDict>(), depth);
            }
            if ffi::PyUnicode_CheckExact(ptr) != 0 {
                return self.text(value.cast_unchecked::<PyString>());
            }
            if ffi::PyList_CheckExact(ptr) != 0 {
                let list = value.cast_unchecked::<PyList>();
                return self.array(list.len(), list.iter().map(Ok), depth, || Ok(list.len()));
            }
            if ffi::PyLong_CheckExact(ptr) != 0 {
                // ctypes c_int64 arguments wrap arbitrary Python integers modulo 2**64.
                let wrapped = ffi::PyLong_AsUnsignedLongLongMask(ptr);
                if wrapped == u64::MAX {
                    if let Some(err) = PyErr::take(self.py) {
                        return Err(err);
                    }
                }
                return Ok(ddwaf_object::signed_val(wrapped as i64));
            }
            if ffi::PyBool_Check(ptr) != 0 {
                return Ok(ddwaf_object::bool_val(ptr == ffi::Py_True()));
            }
            if ffi::PyFloat_CheckExact(ptr) != 0 {
                return Ok(ddwaf_object::float_val(
                    value.cast_unchecked::<PyFloat>().value(),
                ));
            }
            if ffi::PyBytes_CheckExact(ptr) != 0 {
                return self.bytes(value.cast_unchecked::<PyBytes>().as_bytes());
            }
            if ptr == ffi::Py_None() {
                return Ok(ddwaf_object::null());
            }
            if ffi::PyTuple_CheckExact(ptr) != 0 {
                // Same result as the generic Sequence path, without the protocol calls.
                let tuple = value.cast_unchecked::<PyTuple>();
                return self.array(tuple.len(), tuple.iter().map(Ok), depth, || Ok(tuple.len()));
            }
        }
        let (sequence, mapping) = abc_types(self.py)?;
        if value.is_instance(sequence.bind(self.py))? {
            return self.array(value.len()?, value.try_iter()?, depth, || value.len());
        }
        if value.is_instance(mapping.bind(self.py))? {
            return self.mapping(value, depth);
        }
        self.text(&value.str()?)
    }

    /// Effective entry cap for a container at `depth`, recording depth truncation.
    #[inline]
    fn cap(&mut self, depth: i64) -> usize {
        if depth <= 0 {
            bump(
                &mut self.truncation.container_depth,
                DDWAF_MAX_CONTAINER_DEPTH,
            );
            0
        } else {
            self.limits.objects.min(DDWAF_OBJ_MAX_CAPACITY)
        }
    }

    fn container(&self, map: bool, capacity: usize) -> PyResult<Owned<'a>> {
        let mut obj = ddwaf_object::invalid();
        let init = if map {
            self.api.ddwaf_object_set_map
        } else {
            self.api.ddwaf_object_set_array
        };
        // SAFETY: `capacity <= DDWAF_OBJ_MAX_CAPACITY` fits the uint16 parameter.
        let ok = unsafe { init(&mut obj, capacity as u16, self.api.default_allocator) };
        if ok.is_null() {
            return Err(PyMemoryError::new_err(
                "libddwaf container allocation failed",
            ));
        }
        Ok(Owned::new(self.api, obj))
    }

    fn array(
        &mut self,
        len: usize,
        items: impl Iterator<Item = PyResult<Bound<'py, PyAny>>>,
        depth: i64,
        current_len: impl Fn() -> PyResult<usize>,
    ) -> PyResult<ddwaf_object> {
        let max = self.cap(depth);
        let mut array = self.container(false, len.min(max))?;
        for item in items {
            let item = item?;
            // SAFETY: `array` was initialized as an array by `container`.
            let slots = unsafe { &mut array.obj.via.array };
            if slots.size as usize >= max {
                bump(&mut self.truncation.container_size, current_len()?);
                break;
            }
            if slots.size == slots.capacity {
                // The sequence grew while being converted.
                break;
            }
            let child = self.value(&item, depth - 1)?;
            let slots = unsafe { &mut array.obj.via.array };
            // SAFETY: `size < capacity`, so the slot is inside the allocation.
            unsafe { slots.ptr.add(slots.size as usize).write(child) };
            slots.size += 1;
        }
        Ok(array.release())
    }

    #[cfg(not(Py_GIL_DISABLED))]
    fn dict(&mut self, dict: &Bound<'py, PyDict>, depth: i64) -> PyResult<ddwaf_object> {
        let len = dict.len();
        let max = self.cap(depth);
        let mut map = self.container(true, len.min(max))?;
        let mut pos: ffi::Py_ssize_t = 0;
        let mut key: *mut ffi::PyObject = std::ptr::null_mut();
        let mut item: *mut ffi::PyObject = std::ptr::null_mut();
        // SAFETY: attached; borrowed references are promoted to owned ones before any call that
        // can run Python code (fallback `str()`, protocol containers) and mutate the dict.
        while unsafe { ffi::PyDict_Next(dict.as_ptr(), &mut pos, &mut key, &mut item) } != 0 {
            let (key, item) = unsafe {
                (
                    Bound::from_borrowed_ptr(self.py, key),
                    Bound::from_borrowed_ptr(self.py, item),
                )
            };
            if !self.entry(&mut map, max, &key, &item, depth, || Ok(dict.len()))? {
                break;
            }
            if dict.len() != len {
                return Err(PyRuntimeError::new_err(
                    "dictionary changed size during iteration",
                ));
            }
        }
        Ok(map.release())
    }

    #[cfg(Py_GIL_DISABLED)]
    fn dict(&mut self, dict: &Bound<'py, PyDict>, depth: i64) -> PyResult<ddwaf_object> {
        self.mapping(dict.as_any(), depth)
    }

    fn mapping(&mut self, value: &Bound<'py, PyAny>, depth: i64) -> PyResult<ddwaf_object> {
        let max = self.cap(depth);
        let mut map = self.container(true, value.len()?.min(max))?;
        for pair in value.call_method0("items")?.try_iter()? {
            let (key, item): (Bound<'py, PyAny>, Bound<'py, PyAny>) = pair?.extract()?;
            if !self.entry(&mut map, max, &key, &item, depth, || value.len())? {
                break;
            }
        }
        Ok(map.release())
    }

    /// Inserts one entry; returns false once the map is full.
    fn entry(
        &mut self,
        map: &mut Owned<'a>,
        max: usize,
        key: &Bound<'py, PyAny>,
        item: &Bound<'py, PyAny>,
        depth: i64,
        current_len: impl Fn() -> PyResult<usize>,
    ) -> PyResult<bool> {
        let key_ptr = key.as_ptr();
        // Only exact str/bytes keys are kept; the ctypes serializer drops the others.
        let is_text = unsafe { ffi::PyUnicode_CheckExact(key_ptr) } != 0;
        if !is_text && unsafe { ffi::PyBytes_CheckExact(key_ptr) } == 0 {
            return Ok(true);
        }
        // SAFETY: `map` was initialized as a map by `container`.
        let slots = unsafe { &mut map.obj.via.map };
        if slots.size as usize >= max {
            bump(&mut self.truncation.container_size, current_len()?);
            return Ok(false);
        }
        if slots.size == slots.capacity {
            return Ok(false);
        }
        let key = Owned::new(
            self.api,
            if is_text {
                self.text(unsafe { key.cast_unchecked::<PyString>() })?
            } else {
                self.bytes(unsafe { key.cast_unchecked::<PyBytes>() }.as_bytes())?
            },
        );
        let val = self.value(item, depth - 1)?;
        let slots = unsafe { &mut map.obj.via.map };
        // SAFETY: `size < capacity`, so the slot is inside the allocation.
        unsafe {
            slots.ptr.add(slots.size as usize).write(ddwaf_object_kv {
                key: key.release(),
                val,
            })
        };
        slots.size += 1;
        Ok(true)
    }

    fn alloc(&self, len: usize) -> PyResult<*mut u8> {
        // Released by ddwaf_object_destroy as (ptr, size, alignof(char)): the documented
        // contract of allocator-owned (`nocopy`) strings.
        let ptr = unsafe { (self.api.ddwaf_allocator_alloc)(self.api.default_allocator, len, 1) };
        if ptr.is_null() {
            return Err(PyMemoryError::new_err("libddwaf string allocation failed"));
        }
        Ok(ptr.cast())
    }

    /// Truncates by bytes and copies once into the final native storage.
    fn bytes(&mut self, value: &[u8]) -> PyResult<ddwaf_object> {
        if value.len() > self.limits.string {
            bump(&mut self.truncation.string_length, value.len());
        }
        let value = &value[..value.len().min(self.limits.string)];
        if value.len() <= DDWAF_SMALL_STRING_MAX {
            return Ok(ddwaf_object::small_string(value));
        }
        let ptr = self.alloc(value.len())?;
        // SAFETY: `ptr` holds `value.len()` writable bytes that cannot overlap Python storage.
        unsafe { std::ptr::copy_nonoverlapping(value.as_ptr(), ptr, value.len()) };
        Ok(ddwaf_object::heap_string(
            ptr.cast::<c_char>(),
            value.len() as u32,
        ))
    }

    #[cfg(not(any(Py_LIMITED_API, PyPy, GraalPy)))]
    fn text(&mut self, value: &Bound<'py, PyString>) -> PyResult<ddwaf_object> {
        use pyo3::types::PyStringData;
        // Reading the canonical UCS storage avoids PyUnicode_AsUTF8, which would attach a
        // retained UTF-8 copy to every non-ASCII source string.
        // SAFETY: attached; the immutable storage is only read within this call.
        let data = unsafe { value.data()? };
        if let PyStringData::Ucs1(bytes) = data {
            #[cfg(not(Py_3_14))]
            let is_ascii = unsafe { ffi::PyUnicode_IS_ASCII(value.as_ptr()) != 0 };
            #[cfg(Py_3_14)]
            let is_ascii = bytes.is_ascii();
            if is_ascii {
                return self.bytes(bytes);
            }
        }
        #[cfg(not(Py_3_14))]
        {
            // Reuse a UTF-8 cache already populated by another consumer without creating one.
            let compact = unsafe { &*value.as_ptr().cast::<ffi::PyCompactUnicodeObject>() };
            if !compact.utf8.is_null() {
                let utf8 = unsafe {
                    std::slice::from_raw_parts(
                        compact.utf8.cast::<u8>(),
                        compact.utf8_length as usize,
                    )
                };
                return self.bytes(utf8);
            }
        }
        match data {
            PyStringData::Ucs1(data) => self.transcode(data.iter().map(|&c| u32::from(c))),
            PyStringData::Ucs2(data) => self.transcode(data.iter().map(|&c| u32::from(c))),
            PyStringData::Ucs4(data) => self.transcode(data.iter().copied()),
        }
    }

    #[cfg(any(Py_LIMITED_API, PyPy, GraalPy))]
    fn text(&mut self, value: &Bound<'py, PyString>) -> PyResult<ddwaf_object> {
        let encoded = value.call_method1("encode", ("utf-8", "ignore"))?;
        self.bytes(encoded.cast::<PyBytes>()?.as_bytes())
    }

    #[cfg(not(any(Py_LIMITED_API, PyPy, GraalPy)))]
    fn transcode(&mut self, points: impl Iterator<Item = u32> + Clone) -> PyResult<ddwaf_object> {
        let full: usize = points.clone().map(utf8_len).sum();
        if full > self.limits.string {
            bump(&mut self.truncation.string_length, full);
        }
        let len = full.min(self.limits.string);
        if len <= DDWAF_SMALL_STRING_MAX {
            let mut buffer = [0u8; DDWAF_SMALL_STRING_MAX];
            write_utf8(&mut buffer[..len], points);
            return Ok(ddwaf_object::small_string(&buffer[..len]));
        }
        let ptr = self.alloc(len)?;
        // SAFETY: `ptr` holds `len` writable bytes; `write_utf8` fills all of them.
        write_utf8(unsafe { std::slice::from_raw_parts_mut(ptr, len) }, points);
        Ok(ddwaf_object::heap_string(ptr.cast::<c_char>(), len as u32))
    }
}

/// Converts a native value into Python objects, like `ddwaf_object.struct`.
///
/// # Safety
/// `obj` must be a valid, initialized native object.
pub unsafe fn decode<'py>(
    py: Python<'py>,
    api: &Api,
    obj: &ddwaf_object,
) -> PyResult<Bound<'py, PyAny>> {
    unsafe {
        if let Some(bytes) = obj.as_bytes() {
            return decode_str(py, bytes);
        }
        if let Some(entries) = obj.as_map_slice(api) {
            let dict = PyDict::new(py);
            for entry in entries {
                dict.set_item(decode(py, api, &entry.key)?, decode(py, api, &entry.val)?)?;
            }
            return Ok(dict.into_any());
        }
        if let Some(items) = obj.as_array_slice(api) {
            let list = PyList::empty(py);
            for item in items {
                list.append(decode(py, api, item)?)?;
            }
            return Ok(list.into_any());
        }
        Ok(match obj.tag() {
            DDWAF_OBJ_SIGNED => obj.via.i64_.val.into_pyobject(py)?.into_any(),
            DDWAF_OBJ_UNSIGNED => obj.via.u64_.val.into_pyobject(py)?.into_any(),
            DDWAF_OBJ_BOOL => PyBool::new(py, obj.via.b8.val).to_owned().into_any(),
            DDWAF_OBJ_FLOAT => PyFloat::new(py, obj.via.f64_.val).into_any(),
            _ => py.None().into_bound(py),
        })
    }
}

fn decode_str<'py>(py: Python<'py>, bytes: &[u8]) -> PyResult<Bound<'py, PyAny>> {
    // SAFETY: CPython copies the buffer; invalid UTF-8 is ignored like `.decode(errors="ignore")`.
    unsafe {
        Bound::from_owned_ptr_or_err(
            py,
            ffi::PyUnicode_DecodeUTF8(
                bytes.as_ptr().cast(),
                bytes.len() as ffi::Py_ssize_t,
                c"ignore".as_ptr(),
            ),
        )
    }
}

/// The fields of an evaluation result that Python consumers use.
pub struct EvalOutput<'py> {
    pub events: Bound<'py, PyAny>,
    pub actions: Bound<'py, PyAny>,
    pub attributes: Bound<'py, PyAny>,
    pub duration_ns: u64,
    pub timeout: bool,
    pub keep: bool,
}

/// Reads the top-level result map without materializing it as a Python dict.
///
/// # Safety
/// `obj` must be a valid, initialized native object.
pub unsafe fn decode_result<'py>(
    py: Python<'py>,
    api: &Api,
    obj: &ddwaf_object,
) -> PyResult<Option<EvalOutput<'py>>> {
    let Some(entries) = (unsafe { obj.as_map_slice(api) }) else {
        return Ok(None);
    };
    let mut output = EvalOutput {
        events: PyList::empty(py).into_any(),
        actions: PyDict::new(py).into_any(),
        attributes: PyDict::new(py).into_any(),
        duration_ns: 0,
        timeout: false,
        keep: false,
    };
    for entry in entries {
        let value = &entry.val;
        unsafe {
            match entry.key.as_bytes() {
                Some(b"events") => output.events = decode(py, api, value)?,
                Some(b"actions") => output.actions = decode(py, api, value)?,
                Some(b"attributes") => output.attributes = decode(py, api, value)?,
                Some(b"duration") if value.tag() == DDWAF_OBJ_UNSIGNED => {
                    output.duration_ns = value.via.u64_.val
                }
                Some(b"timeout") if value.tag() == DDWAF_OBJ_BOOL => {
                    output.timeout = value.via.b8.val
                }
                Some(b"keep") if value.tag() == DDWAF_OBJ_BOOL => output.keep = value.via.b8.val,
                _ => {}
            }
        }
    }
    Ok(Some(output))
}

/// Raw native view of a value (tags and exact bytes), for differential tests against ctypes.
///
/// # Safety
/// `obj` must be a valid, initialized native object.
pub unsafe fn describe<'py>(
    py: Python<'py>,
    api: &Api,
    obj: &ddwaf_object,
) -> PyResult<Bound<'py, PyAny>> {
    unsafe {
        if let Some(bytes) = obj.as_bytes() {
            return Ok(("string", PyBytes::new(py, bytes))
                .into_pyobject(py)?
                .into_any());
        }
        if let Some(entries) = obj.as_map_slice(api) {
            let list = PyList::empty(py);
            for entry in entries {
                list.append((
                    describe(py, api, &entry.key)?,
                    describe(py, api, &entry.val)?,
                ))?;
            }
            return Ok(("map", list).into_pyobject(py)?.into_any());
        }
        if let Some(items) = obj.as_array_slice(api) {
            let list = PyList::empty(py);
            for item in items {
                list.append(describe(py, api, item)?)?;
            }
            return Ok(("array", list).into_pyobject(py)?.into_any());
        }
        Ok(match obj.tag() {
            DDWAF_OBJ_SIGNED => ("signed", obj.via.i64_.val).into_pyobject(py)?.into_any(),
            DDWAF_OBJ_UNSIGNED => ("unsigned", obj.via.u64_.val).into_pyobject(py)?.into_any(),
            DDWAF_OBJ_BOOL => ("bool", obj.via.b8.val).into_pyobject(py)?.into_any(),
            DDWAF_OBJ_FLOAT => ("float", obj.via.f64_.val.to_bits())
                .into_pyobject(py)?
                .into_any(),
            DDWAF_OBJ_NULL => ("null",).into_pyobject(py)?.into_any(),
            _ => ("invalid",).into_pyobject(py)?.into_any(),
        })
    }
}
