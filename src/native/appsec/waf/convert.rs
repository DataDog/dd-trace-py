//! Python <-> `ddwaf_object` conversion.
//!
//! Encoding walks Python containers once and writes straight into libddwaf storage allocated
//! with libddwaf-rust's allocator: each string is copied (or transcoded) exactly once, from
//! the Python buffer into its final native location. Decoding reads native results directly
//! into Python objects, without an intermediate Rust tree.
//!
//! Coercion, limits and truncation reporting mirror the ctypes serializer in
//! `ddtrace/appsec/_ddwaf/ddwaf_types.py`, which stays the differential oracle in tests.

use libddwaf::object::*;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyList, PyString, PyTuple};

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

pub struct Encoder<'py> {
    py: Python<'py>,
    limits: Limits,
    pub truncation: Truncation,
}

impl<'py> Encoder<'py> {
    pub fn new(py: Python<'py>, limits: Limits) -> Self {
        Self {
            py,
            limits,
            truncation: Truncation::default(),
        }
    }

    /// On success the caller owns the returned object; on error nothing is leaked.
    pub fn encode(&mut self, value: &Bound<'py, PyAny>) -> PyResult<WafObject> {
        self.value(value, self.limits.depth)
    }

    fn value(&mut self, value: &Bound<'py, PyAny>, depth: i64) -> PyResult<WafObject> {
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
                return Ok(WafSigned::new(wrapped as i64).into());
            }
            if ffi::PyBool_Check(ptr) != 0 {
                return Ok(WafBool::new(ptr == ffi::Py_True()).into());
            }
            if ffi::PyFloat_CheckExact(ptr) != 0 {
                return Ok(WafFloat::new(value.cast_unchecked::<PyFloat>().value()).into());
            }
            if ffi::PyBytes_CheckExact(ptr) != 0 {
                return self.bytes(value.cast_unchecked::<PyBytes>().as_bytes());
            }
            if ptr == ffi::Py_None() {
                return Ok(WafNull::new().into());
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
            self.limits.objects.min(usize::from(u16::MAX))
        }
    }

    fn array(
        &mut self,
        len: usize,
        items: impl Iterator<Item = PyResult<Bound<'py, PyAny>>>,
        depth: i64,
        current_len: impl Fn() -> PyResult<usize>,
    ) -> PyResult<WafObject> {
        let max = self.cap(depth);
        let mut array =
            WafArray::new(len.min(max)).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let mut count = 0;
        for item in items {
            let item = item?;
            if count >= max {
                bump(&mut self.truncation.container_size, current_len()?);
                break;
            }
            if count == array.len() {
                // The sequence grew while being converted.
                break;
            }
            *array.iter_mut().nth(count).expect("allocated slot") = self.value(&item, depth - 1)?;
            count += 1;
        }
        array.truncate(count);
        Ok(array.into())
    }

    #[cfg(not(Py_GIL_DISABLED))]
    fn dict(&mut self, dict: &Bound<'py, PyDict>, depth: i64) -> PyResult<WafObject> {
        let len = dict.len();
        let max = self.cap(depth);
        let mut map =
            WafMap::new(len.min(max)).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let mut count = 0;
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
            if !self.entry(&mut map, &mut count, max, (&key, &item), depth, || {
                Ok(dict.len())
            })? {
                break;
            }
            if dict.len() != len {
                return Err(PyRuntimeError::new_err(
                    "dictionary changed size during iteration",
                ));
            }
        }
        map.truncate(count);
        Ok(map.into())
    }

    #[cfg(Py_GIL_DISABLED)]
    fn dict(&mut self, dict: &Bound<'py, PyDict>, depth: i64) -> PyResult<WafObject> {
        self.mapping(dict.as_any(), depth)
    }

    fn mapping(&mut self, value: &Bound<'py, PyAny>, depth: i64) -> PyResult<WafObject> {
        let max = self.cap(depth);
        let mut map =
            WafMap::new(value.len()?.min(max)).map_err(|e| PyValueError::new_err(e.to_string()))?;
        let mut count = 0;
        for pair in value.call_method0("items")?.try_iter()? {
            let (key, item): (Bound<'py, PyAny>, Bound<'py, PyAny>) = pair?.extract()?;
            if !self.entry(&mut map, &mut count, max, (&key, &item), depth, || {
                value.len()
            })? {
                break;
            }
        }
        map.truncate(count);
        Ok(map.into())
    }

    /// Inserts one entry; returns false once the map is full.
    fn entry(
        &mut self,
        map: &mut WafMap,
        count: &mut usize,
        max: usize,
        pair: (&Bound<'py, PyAny>, &Bound<'py, PyAny>),
        depth: i64,
        current_len: impl Fn() -> PyResult<usize>,
    ) -> PyResult<bool> {
        let (key, item) = pair;
        let key_ptr = key.as_ptr();
        // Only exact str/bytes keys are kept; the ctypes serializer drops the others.
        let is_text = unsafe { ffi::PyUnicode_CheckExact(key_ptr) } != 0;
        if !is_text && unsafe { ffi::PyBytes_CheckExact(key_ptr) } == 0 {
            return Ok(true);
        }
        if *count >= max {
            bump(&mut self.truncation.container_size, current_len()?);
            return Ok(false);
        }
        if *count == map.len() {
            return Ok(false);
        }
        let key = if is_text {
            self.text(unsafe { key.cast_unchecked::<PyString>() })?
        } else {
            self.bytes(unsafe { key.cast_unchecked::<PyBytes>() }.as_bytes())?
        };
        let val = self.value(item, depth - 1)?;
        *map.iter_mut().nth(*count).expect("allocated slot") = Keyed::new(key, val);
        *count += 1;
        Ok(true)
    }

    /// Truncates by bytes and copies once into the final native storage.
    fn bytes(&mut self, value: &[u8]) -> PyResult<WafObject> {
        if value.len() > self.limits.string {
            bump(&mut self.truncation.string_length, value.len());
        }
        let value = &value[..value.len().min(self.limits.string)];
        Ok(WafString::new(value).expect("bounded string length").into())
    }

    #[cfg(not(any(Py_LIMITED_API, PyPy, GraalPy)))]
    fn text(&mut self, value: &Bound<'py, PyString>) -> PyResult<WafObject> {
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
    fn text(&mut self, value: &Bound<'py, PyString>) -> PyResult<WafObject> {
        let encoded = value.call_method1("encode", ("utf-8", "ignore"))?;
        self.bytes(encoded.cast::<PyBytes>()?.as_bytes())
    }

    #[cfg(not(any(Py_LIMITED_API, PyPy, GraalPy)))]
    fn transcode(&mut self, points: impl Iterator<Item = u32> + Clone) -> PyResult<WafObject> {
        let full: usize = points.clone().map(utf8_len).sum();
        if full > self.limits.string {
            bump(&mut self.truncation.string_length, full);
        }
        let len = full.min(self.limits.string);
        Ok(WafString::new_with(len, |out| write_utf8(out, points))
            .expect("bounded string length")
            .into())
    }
}

/// Converts a native value directly into Python objects.
pub fn decode<'py>(py: Python<'py>, obj: &WafObject) -> PyResult<Bound<'py, PyAny>> {
    if let Some(text) = obj.as_type::<WafString>() {
        return decode_str(py, text.as_bytes());
    }
    if let Some(map) = obj.as_type::<WafMap>() {
        let dict = PyDict::new(py);
        for entry in map.iter() {
            dict.set_item(decode(py, entry.key())?, decode(py, entry.value())?)?;
        }
        return Ok(dict.into_any());
    }
    if let Some(array) = obj.as_type::<WafArray>() {
        let list = PyList::empty(py);
        for item in array.iter() {
            list.append(decode(py, item)?)?;
        }
        return Ok(list.into_any());
    }
    Ok(match obj.object_type() {
        WafObjectType::Signed => obj.to_i64().unwrap().into_pyobject(py)?.into_any(),
        WafObjectType::Unsigned => obj.to_u64().unwrap().into_pyobject(py)?.into_any(),
        WafObjectType::Bool => PyBool::new(py, obj.to_bool().unwrap())
            .to_owned()
            .into_any(),
        WafObjectType::Float => PyFloat::new(py, obj.to_f64().unwrap()).into_any(),
        _ => py.None().into_bound(py),
    })
}

fn decode_str<'py>(py: Python<'py>, bytes: &[u8]) -> PyResult<Bound<'py, PyAny>> {
    // SAFETY: CPython copies the buffer; invalid UTF-8 is ignored like .decode(errors="ignore").
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

/// Raw native view (types and exact bytes) for differential tests against ctypes.
pub fn describe<'py>(py: Python<'py>, obj: &WafObject) -> PyResult<Bound<'py, PyAny>> {
    if let Some(text) = obj.as_type::<WafString>() {
        return Ok(("string", PyBytes::new(py, text.as_bytes()))
            .into_pyobject(py)?
            .into_any());
    }
    if let Some(map) = obj.as_type::<WafMap>() {
        let list = PyList::empty(py);
        for entry in map.iter() {
            list.append((describe(py, entry.key())?, describe(py, entry.value())?))?;
        }
        return Ok(("map", list).into_pyobject(py)?.into_any());
    }
    if let Some(array) = obj.as_type::<WafArray>() {
        let list = PyList::empty(py);
        for item in array.iter() {
            list.append(describe(py, item)?)?;
        }
        return Ok(("array", list).into_pyobject(py)?.into_any());
    }
    Ok(match obj.object_type() {
        WafObjectType::Signed => ("signed", obj.to_i64().unwrap())
            .into_pyobject(py)?
            .into_any(),
        WafObjectType::Unsigned => ("unsigned", obj.to_u64().unwrap())
            .into_pyobject(py)?
            .into_any(),
        WafObjectType::Bool => ("bool", obj.to_bool().unwrap())
            .into_pyobject(py)?
            .into_any(),
        WafObjectType::Float => ("float", obj.to_f64().unwrap().to_bits())
            .into_pyobject(py)?
            .into_any(),
        WafObjectType::Null => ("null",).into_pyobject(py)?.into_any(),
        _ => ("invalid",).into_pyobject(py)?.into_any(),
    })
}
