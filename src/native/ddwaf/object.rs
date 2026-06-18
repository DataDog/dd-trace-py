//! `ddwaf_object` bindings: a one-for-one mirror of the libddwaf C object API that the
//! previous ctypes layer (`ddtrace/appsec/_ddwaf/ddwaf_types.py`) declared.
//!
//! The Python side keeps ALL of the recursive build/read logic; this module only exposes:
//!   * [`DDWafObject`] — an opaque wrapper around a raw `*mut ddwaf_object` (either an owned
//!     16-byte struct we allocated, or a non-owning *slot* view into a parent container), and
//!   * thin module-level functions mirroring `ddwaf_object_set_*` / `ddwaf_object_insert*` /
//!     `ddwaf_object_from_json` / `ddwaf_object_destroy`, plus read accessors used by the Python
//!     `.struct` reader (which can no longer read the union memory directly).
//!
//! AIDEV-NOTE: ownership mirrors the old ctypes layout exactly. An owned `DDWafObject` owns only
//! its 16-byte struct box; the *inner* heap data (string buffers, array/map storage) is freed
//! explicitly by the Python side via `ddwaf_object_destroy` — never automatically on Drop. This is
//! required because on `DDWAF_OK`/`DDWAF_MATCH` the WAF takes ownership of the input object's inner
//! data, so auto-destroying it on Drop would double-free. Borrowed slots never free anything.
//! Borrowed slots are only valid while the owning root is alive AND its containers are not regrown
//! (the Python builder pre-reserves capacity, so no reallocation occurs — same invariant the ctypes
//! code relied on).

use std::ffi::c_char;

use libddwaf_sys as sys;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

/// Opaque wrapper around the libddwaf default allocator.
///
/// The default allocator is a process-wide singleton pointer: always valid and never destroyed.
#[pyclass(
    name = "DDWafAllocator",
    module = "ddtrace.internal.native._native.ddwaf",
    frozen
)]
pub struct DDWafAllocator {
    pub(crate) raw: sys::ddwaf_allocator,
}
// SAFETY: the default allocator is an immutable, process-wide singleton.
unsafe impl Send for DDWafAllocator {}
unsafe impl Sync for DDWafAllocator {}

/// Wrapper around a raw `ddwaf_object`. See the module-level note for the ownership model.
#[pyclass(name = "DDWafObject", module = "ddtrace.internal.native._native.ddwaf")]
pub struct DDWafObject {
    pub(crate) ptr: *mut sys::ddwaf_object,
    /// True only for the top-level struct box we allocated; false for slot views into a parent.
    owned: bool,
}
// SAFETY: all access is serialized by the GIL and, for contexts, by the Python-side capsule lock.
unsafe impl Send for DDWafObject {}
unsafe impl Sync for DDWafObject {}

impl DDWafObject {
    /// Wraps a non-owning slot pointer returned by libddwaf (insert / array / map element).
    fn borrowed(ptr: *mut sys::ddwaf_object) -> Self {
        DDWafObject { ptr, owned: false }
    }
}

#[pymethods]
impl DDWafObject {
    /// Allocates a new, owned, zero-initialised (INVALID) object.
    #[new]
    fn new() -> Self {
        let boxed = Box::new(sys::ddwaf_object::default());
        DDWafObject {
            ptr: Box::into_raw(boxed),
            owned: true,
        }
    }

    /// Raw libddwaf type tag (the `DDWAF_OBJ_TYPE` discriminant, e.g. 0x10/0x12/0x14 for the three
    /// string variants). Kept raw so the Python `DDWAF_OBJ_TYPE` IntEnum comparisons are unchanged.
    #[getter]
    fn r#type(&self) -> u8 {
        // SAFETY: `ptr` is valid for the lifetime of this wrapper / its owning root.
        unsafe { (*self.ptr).type_ }
    }

    fn __bool__(&self) -> bool {
        !self.ptr.is_null()
    }
}

impl Drop for DDWafObject {
    fn drop(&mut self) {
        // Free only the 16-byte struct box for owned objects. Inner heap data is freed explicitly
        // by the Python side via `ddwaf_object_destroy` (see module note); slots free nothing.
        if self.owned && !self.ptr.is_null() {
            // SAFETY: `ptr` came from `Box::into_raw` in `new`.
            unsafe { drop(Box::from_raw(self.ptr)) };
        }
    }
}

#[pyfunction]
pub fn ddwaf_get_default_allocator() -> DDWafAllocator {
    DDWafAllocator {
        raw: unsafe { sys::ddwaf_get_default_allocator() },
    }
}

//
// Builders (mirror the libddwaf 2.0 set_* / insert API). Each is a thin, GIL-held wrapper; the
// per-element calls are tiny so we do not release the GIL here.
//

#[pyfunction]
pub fn ddwaf_object_set_string(
    obj: &DDWafObject,
    string: &[u8],
    length: u32,
    alloc: &DDWafAllocator,
) {
    unsafe {
        sys::ddwaf_object_set_string(obj.ptr, string.as_ptr() as *const c_char, length, alloc.raw)
    };
}

#[pyfunction]
pub fn ddwaf_object_set_signed(obj: &DDWafObject, value: i64) {
    unsafe { sys::ddwaf_object_set_signed(obj.ptr, value) };
}

#[pyfunction]
pub fn ddwaf_object_set_unsigned(obj: &DDWafObject, value: u64) {
    unsafe { sys::ddwaf_object_set_unsigned(obj.ptr, value) };
}

#[pyfunction]
pub fn ddwaf_object_set_bool(obj: &DDWafObject, value: bool) {
    unsafe { sys::ddwaf_object_set_bool(obj.ptr, value) };
}

#[pyfunction]
pub fn ddwaf_object_set_float(obj: &DDWafObject, value: f64) {
    unsafe { sys::ddwaf_object_set_float(obj.ptr, value) };
}

#[pyfunction]
pub fn ddwaf_object_set_null(obj: &DDWafObject) {
    unsafe { sys::ddwaf_object_set_null(obj.ptr) };
}

#[pyfunction]
pub fn ddwaf_object_set_array(obj: &DDWafObject, capacity: u16, alloc: &DDWafAllocator) {
    unsafe { sys::ddwaf_object_set_array(obj.ptr, capacity, alloc.raw) };
}

#[pyfunction]
pub fn ddwaf_object_set_map(obj: &DDWafObject, capacity: u16, alloc: &DDWafAllocator) {
    unsafe { sys::ddwaf_object_set_map(obj.ptr, capacity, alloc.raw) };
}

/// Inserts a new (uninitialised) element into an array, returning a non-owning view of the slot.
#[pyfunction]
pub fn ddwaf_object_insert(obj: &DDWafObject, alloc: &DDWafAllocator) -> DDWafObject {
    let slot = unsafe { sys::ddwaf_object_insert(obj.ptr, alloc.raw) };
    DDWafObject::borrowed(slot)
}

/// Inserts a new keyed (uninitialised) element into a map, returning a non-owning view of the slot.
#[pyfunction]
pub fn ddwaf_object_insert_key(
    obj: &DDWafObject,
    key: &[u8],
    length: u32,
    alloc: &DDWafAllocator,
) -> DDWafObject {
    let slot = unsafe {
        sys::ddwaf_object_insert_key(obj.ptr, key.as_ptr() as *const c_char, length, alloc.raw)
    };
    DDWafObject::borrowed(slot)
}

/// Parses a JSON document into `obj`. Releases the GIL (ruleset parsing can be expensive).
#[pyfunction]
pub fn ddwaf_object_from_json(
    py: Python<'_>,
    obj: &DDWafObject,
    json: &[u8],
    length: u32,
    alloc: &DDWafAllocator,
) -> bool {
    let out = obj.ptr as usize;
    let alloc = alloc.raw as usize;
    // Copy the JSON bytes so we don't hold a GIL-bound borrow across the detached call.
    let data = json.to_vec();
    py.detach(move || unsafe {
        sys::ddwaf_object_from_json(
            out as *mut sys::ddwaf_object,
            data.as_ptr() as *const c_char,
            length,
            alloc as sys::ddwaf_allocator,
        )
    })
}

/// Frees the *inner* heap data of `obj` (mirrors the old `ddwaf_object_free`). The 16-byte struct
/// box itself is freed when the wrapper is garbage-collected (see module note).
#[pyfunction]
pub fn ddwaf_object_destroy(obj: &DDWafObject, alloc: &DDWafAllocator) {
    if obj.ptr.is_null() {
        return;
    }
    unsafe { sys::ddwaf_object_destroy(obj.ptr, alloc.raw) };
}

//
// Read accessors used by the Python `.struct` reader. The dispatch tree stays in Python; these are
// the leaves that replace the previous direct ctypes union reads.
//

#[pyfunction]
pub fn ddwaf_object_get_signed(obj: &DDWafObject) -> i64 {
    unsafe { (*obj.ptr).via.i64_.val }
}

#[pyfunction]
pub fn ddwaf_object_get_unsigned(obj: &DDWafObject) -> u64 {
    unsafe { (*obj.ptr).via.u64_.val }
}

#[pyfunction]
pub fn ddwaf_object_get_bool(obj: &DDWafObject) -> bool {
    unsafe { (*obj.ptr).via.b8.val }
}

#[pyfunction]
pub fn ddwaf_object_get_float(obj: &DDWafObject) -> f64 {
    unsafe { (*obj.ptr).via.f64_.val }
}

/// Returns the raw bytes of a string object, transparently handling the heap, literal, and inline
/// "small" string variants (collapses the three previous ctypes branches into one). Decoding to
/// `str` stays on the Python side.
#[pyfunction]
pub fn ddwaf_object_get_bytes<'py>(py: Python<'py>, obj: &DDWafObject) -> Bound<'py, PyBytes> {
    let bytes: &[u8] = unsafe {
        let o = &*obj.ptr;
        let t = o.type_ as u32;
        if t == sys::DDWAF_OBJ_SMALL_STRING {
            let ss = &o.via.sstr;
            std::slice::from_raw_parts(ss.data.as_ptr() as *const u8, ss.size as usize)
        } else {
            // DDWAF_OBJ_STRING / DDWAF_OBJ_LITERAL_STRING
            let s = o.via.str_;
            if s.ptr.is_null() || s.size == 0 {
                &[]
            } else {
                std::slice::from_raw_parts(s.ptr as *const u8, s.size as usize)
            }
        }
    };
    PyBytes::new(py, bytes)
}

#[pyfunction]
pub fn ddwaf_object_array_len(obj: &DDWafObject) -> usize {
    unsafe { (*obj.ptr).via.array.size as usize }
}

#[pyfunction]
pub fn ddwaf_object_array_get(obj: &DDWafObject, index: usize) -> DDWafObject {
    let elem = unsafe { (*obj.ptr).via.array.ptr.add(index) };
    DDWafObject::borrowed(elem)
}

#[pyfunction]
pub fn ddwaf_object_map_len(obj: &DDWafObject) -> usize {
    unsafe { (*obj.ptr).via.map.size as usize }
}

#[pyfunction]
pub fn ddwaf_object_map_key(obj: &DDWafObject, index: usize) -> DDWafObject {
    let kv = unsafe { (*obj.ptr).via.map.ptr.add(index) };
    DDWafObject::borrowed(unsafe { &mut (*kv).key as *mut sys::ddwaf_object })
}

#[pyfunction]
pub fn ddwaf_object_map_value(obj: &DDWafObject, index: usize) -> DDWafObject {
    let kv = unsafe { (*obj.ptr).via.map.ptr.add(index) };
    DDWafObject::borrowed(unsafe { &mut (*kv).val as *mut sys::ddwaf_object })
}

/// Registers the object types and functions on the `ddwaf` submodule.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DDWafAllocator>()?;
    m.add_class::<DDWafObject>()?;
    m.add_function(wrap_pyfunction!(ddwaf_get_default_allocator, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_set_string, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_set_signed, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_set_unsigned, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_set_bool, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_set_float, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_set_null, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_set_array, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_set_map, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_insert, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_insert_key, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_from_json, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_destroy, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_get_signed, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_get_unsigned, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_get_bool, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_get_float, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_get_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_array_len, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_array_get, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_map_len, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_map_key, m)?)?;
    m.add_function(wrap_pyfunction!(ddwaf_object_map_value, m)?)?;
    Ok(())
}
