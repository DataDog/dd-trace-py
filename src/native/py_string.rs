use std::{borrow::Borrow, ops::Deref as _};

use libdd_trace_utils::span::{SpanBytes, SpanText, TraceData};
use pyo3::{
    exceptions::PyValueError,
    types::{PyAnyMethods as _, PyBytesMethods as _, PyString, PyStringMethods as _},
    Bound, Py, PyAny, PyErr, Python,
};
use serde::Serialize;

/// Manual tag identifying which backing storage `view_ptr`/`view_len` describe and
/// what `py_object` holds. See the layout comment on [`PyBackedString`].
#[repr(u32)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Tag {
    /// `view_ptr` points into a `&'static str`. No backing storage to manage.
    Static = 0,
    /// `py_object` owns a Python reference (a `PyString`, `PyBytes`, or `PyNone`).
    /// `view_ptr`/`view_len` are a view into that object's buffer.
    PyObject = 1,
    /// `view_ptr`/`view_len` describe a leaked `Box<str>` we own; the box is
    /// reconstructed from `(view_ptr, view_len)` for drop/clone.
    Rust = 2,
}

/// A Python bytes/str backed utf-8 string we can read without needing access to the GIL
/// that can be put in a libdatadog span.
///
/// ## Storage layout
///
/// The struct uses manual tagging instead of an enum carrying the owned data, so it
/// stays small enough to embed in the many span string fields:
///
/// ```text
/// view_ptr:   *const u8            // 8  — view into the backing bytes
/// view_len:   u32                  // 4  — length in bytes (strings are < 4 GB)
/// tag:        Tag (repr u32)       // 4  — which backing storage is in use
/// py_object:  Option<Py<PyAny>>    // 8  — owned Python ref (niche-optimized; None unless tag == PyObject)
/// ```
///
/// `view_ptr` can point into:
/// - a `&'static str` (`tag == Static`),
/// - the data of a leaked `Box<str>` we own (`tag == Rust`), or
/// - a Python `PyString`/`PyBytes`/`PyNone` buffer (`tag == PyObject`).
///
/// Invariant: `tag == PyObject` iff `py_object.is_some()`. `tag` is the single
/// source of truth; the `Option` only exists so pyo3 manages the Python reference
/// safely (GIL-aware drop/clone) without us hand-rolling `Py_INCREF`/`Py_DECREF`.
pub struct PyBackedString {
    /// Memory view over the backing bytes: a static str, a leaked `Box<str>`, or a
    /// Python object's buffer.
    view_ptr: *const u8,
    /// Length of the view in bytes.
    ///
    /// ASSUMPTION: span string fields are well under 4 GB. We store the length as
    /// `u32`, so a string longer than `u32::MAX` bytes (~4 GiB) is **silently
    /// truncated** here — `view_len` would hold only the low 32 bits
    view_len: u32,
    /// Identifies which backing storage is in use; see [`Tag`].
    tag: Tag,
    /// Owned Python reference. `Some` only when `tag == PyObject`; `None` otherwise.
    /// Kept alive so `view_ptr` stays valid. Stored as `Option<Py<PyAny>>` so pyo3
    /// handles the GIL-aware drop/clone (deferred decref when the GIL isn't held).
    py_object: Option<Py<PyAny>>,
}

impl PyBackedString {
    /// Build a `Rust`-tagged value from a `Box<str>`, leaking the box and recording
    /// its data pointer/length so it can be reconstructed for drop/clone.
    #[inline]
    fn from_box(b: Box<str>) -> Self {
        let view_ptr = b.as_ptr();
        // Truncating cast: assumes `s.len() <= u32::MAX` (see the `view_len` field
        // doc). A >4 GiB string would be silently truncated and corrupt the box
        // reconstruction in `Drop`.
        let view_len = b.len() as u32;
        // The box's allocation is intentionally leaked here. We own it through
        // (view_ptr, view_len) and will reconstruct it in Drop/clone_ref.
        std::mem::forget::<Box<str>>(b);
        Self {
            view_ptr,
            view_len,
            tag: Tag::Rust,
            py_object: None,
        }
    }

    pub fn clone_ref<'py>(&self, py: Python<'py>) -> Self {
        match self.tag {
            Tag::Static => Self {
                view_ptr: self.view_ptr,
                view_len: self.view_len,
                tag: Tag::Static,
                py_object: None,
            },
            Tag::Rust => {
                // Allocate a new Box from thr string view
                Self::from_box(Box::<str>::from(self.deref()))
            },
            Tag::PyObject => Self {
                view_ptr: self.view_ptr,
                view_len: self.view_len,
                tag: Tag::PyObject,
                // SAFETY: tag == PyObject => py_object is Some. clone_ref increfs a
                // fresh reference (GIL is held by the caller's `py` token).
                py_object: self.py_object.as_ref().map(|p| p.clone_ref(py)),
            },
        }
    }

    /// Check if this `PyBackedString` represents Python `None` (not just an empty string).
    ///
    /// Returns `true` only if the storage holds Python's `None` object.
    /// Returns `false` for empty strings created from static data or Python empty strings.
    #[inline(always)]
    pub fn is_py_none(&self, py: Python<'_>) -> bool {
        // SAFETY: `tag == Tag::PyObject` holds iff `py_object` is `Some` (the
        // struct invariant), so the `unwrap()` cannot panic. `is_none` compares
        // against the None singleton.
        self.tag == Tag::PyObject && self.py_object.as_ref().unwrap().is_none(py)
    }

    pub fn py_none<'py>(py: Python<'py>) -> Self {
        Self {
            view_ptr: "".as_ptr(),
            view_len: 0,
            tag: Tag::PyObject,
            py_object: Some(py.None()),
        }
    }

    /// Get the underlying Python object directly for zero-copy semantics.
    ///
    /// Returns the stored Python object if available (PyString, PyBytes, or PyNone),
    /// or creates an interned Python string for static and owned Rust strings.
    #[inline(always)]
    pub fn as_py<'py>(&self, py: Python<'py>) -> Bound<'py, PyAny> {
        match self.tag {
            // SAFETY: `tag == Tag::PyObject` holds iff `py_object` is `Some`
            // (struct invariant), so the `unwrap()` cannot panic. bind() borrows
            // the stored ref as a Bound; .clone() increfs a fresh reference for the
            // caller, leaving our own refcount unchanged (zero-copy out, no
            // transfer of our ownership).
            Tag::PyObject => self.py_object.as_ref().unwrap().bind(py).clone(),
            Tag::Static | Tag::Rust => PyString::intern(py, self.deref()).into_any(),
        }
    }

    /// Visit the held Python object (if any) for the cyclic GC.
    ///
    /// Storage is always a `PyString`, `PyBytes`, or `PyNone` — atomic types that
    /// cannot themselves be part of cycles, so visiting is technically optional
    /// for cycle-breaking. We still visit for correct refcount accounting from
    /// CPython's perspective.
    #[inline(always)]
    pub fn traverse(&self, visit: &pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        if let Some(obj) = &self.py_object {
            visit.call(obj)?;
        }
        Ok(())
    }
}

impl Drop for PyBackedString {
    #[inline]
    fn drop(&mut self) {
        // Only the Rust case needs manual cleanup: the Box<str> is leaked into
        // (view_ptr, view_len) and no field owns it. Static has nothing to free,
        // and PyObject is released by `py_object`'s own (GIL-aware) Drop.
        if self.tag == Tag::Rust {
            // SAFETY: view_ptr/view_len describe a leaked Box<str> we own (see
            // from_box). Reconstruct the box and let it drop to free the allocation.
            unsafe {
                let bytes = std::slice::from_raw_parts(self.view_ptr, self.view_len as usize);
                let s = std::str::from_utf8_unchecked(bytes);
                drop(Box::from_raw(s as *const str as *mut str));
            }
        }
    }
}

impl<'py> pyo3::FromPyObject<'_, 'py> for PyBackedString {
    type Error = pyo3::PyErr;

    #[inline(always)]
    fn extract(obj: pyo3::Borrowed<'_, 'py, PyAny>) -> pyo3::PyResult<Self> {
        let py = obj.py();
        // Fast path: check for string first since it's the most common case
        if let Ok(py_string) = obj.cast::<pyo3::types::PyString>() {
            return Self::try_from(py_string.to_owned());
        }
        // Fallback: check for bytes
        if let Ok(py_bytes) = obj.cast::<pyo3::types::PyBytes>() {
            return Self::try_from(py_bytes.to_owned());
        }
        // Check for None last (least common in hot path)
        if obj.is_none() {
            return Ok(Self::py_none(py));
        }
        Err(PyErr::new::<PyValueError, _>(
            "argument needs to be either a 'str', utf8 encoded 'bytes', or 'None'",
        ))
    }
}

impl<'py> pyo3::IntoPyObject<'py> for &PyBackedString {
    type Target = pyo3::types::PyAny;

    type Output = pyo3::Bound<'py, Self::Target>;

    type Error = std::convert::Infallible;

    #[inline]
    fn into_pyobject(self, py: pyo3::Python<'py>) -> Result<Self::Output, Self::Error> {
        Ok(self.as_py(py))
    }
}

// PyBackedString can be safely shared between threads because:
// 1. Python str (PyUnicode) and bytes objects are immutable after creation
// 2. The `py_object` field keeps the Python object alive, preventing deallocation
// 3. For PyString, `to_str()` returns a pointer to either:
//    - The compact ASCII buffer (for ASCII-only strings), or
//    - A UTF-8 cache that's lazily created and stored on the object
//    Both are stable for the lifetime of the PyUnicode object.
// 4. For PyBytes, the internal buffer is immutable and stable.
// 5. For Rust-tagged values, the leaked Box<str> is owned and stable until drop.
// 6. The Python reference is owned via `Option<Py<PyAny>>`, whose Drop is GIL-aware
//    (it defers the decref when the GIL isn't held), so dropping on any thread is sound.
unsafe impl Sync for PyBackedString {}
unsafe impl Send for PyBackedString {}

impl TryFrom<pyo3::Bound<'_, pyo3::types::PyString>> for PyBackedString {
    type Error = pyo3::PyErr;
    fn try_from(py_string: pyo3::Bound<'_, pyo3::types::PyString>) -> Result<Self, Self::Error> {
        let s = py_string.to_str()?;
        let view_ptr = s.as_ptr();
        // Truncating cast: assumes `s.len() <= u32::MAX` (see `view_len` field doc).
        let view_len = s.len() as u32;
        // Transfer the bound's owned reference into `py_object` (no refcount change):
        // unbind -> Py<PyString>, into_any -> Py<PyAny>.
        let py_object = Some(py_string.unbind().into_any());
        Ok(Self {
            view_ptr,
            view_len,
            tag: Tag::PyObject,
            py_object,
        })
    }
}

impl TryFrom<pyo3::Bound<'_, pyo3::types::PyBytes>> for PyBackedString {
    type Error = pyo3::PyErr;
    fn try_from(py_bytes: pyo3::Bound<'_, pyo3::types::PyBytes>) -> Result<Self, Self::Error> {
        let s = std::str::from_utf8(py_bytes.as_bytes())
            .map_err(|_e| pyo3::PyErr::new::<PyValueError, _>("'bytes' are not utf8 encoded"))?;
        let view_ptr = s.as_ptr();
        // Truncating cast: assumes `s.len() <= u32::MAX` (see `view_len` field doc).
        let view_len = s.len() as u32;
        let py_object = Some(py_bytes.unbind().into_any());
        Ok(Self {
            view_ptr,
            view_len,
            tag: Tag::PyObject,
            py_object,
        })
    }
}

impl std::ops::Deref for PyBackedString {
    type Target = str;

    #[inline]
    fn deref(&self) -> &Self::Target {
        // SAFETY: view_ptr always points to valid UTF-8 of length view_len for the
        // lifetime of this value: a 'static str (Static), a Box<str> we own (Rust),
        // or a validated UTF-8 buffer kept alive by py_object (PyObject / None).
        unsafe {
            let bytes = std::slice::from_raw_parts(self.view_ptr, self.view_len as usize);
            std::str::from_utf8_unchecked(bytes)
        }
    }
}

impl std::hash::Hash for PyBackedString {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        self.deref().hash(state);
    }
}

impl serde::Serialize for PyBackedString {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        self.deref().serialize(serializer)
    }
}

impl std::borrow::Borrow<str> for PyBackedString {
    fn borrow(&self) -> &str {
        self.deref()
    }
}

impl PartialEq for PyBackedString {
    fn eq(&self, other: &Self) -> bool {
        self.deref() == other.deref()
    }
}

impl Eq for PyBackedString {}

impl Default for PyBackedString {
    fn default() -> Self {
        Self::from_static_str("")
    }
}

impl std::fmt::Debug for PyBackedString {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.deref().fmt(f)
    }
}

impl SpanText for PyBackedString {
    fn from_static_str(value: &'static str) -> Self {
        Self {
            view_ptr: value.as_ptr(),
            view_len: value.len() as u32,
            tag: Tag::Static,
            py_object: None,
        }
    }

    fn from_owned(value: String) -> Self {
        Self::from_box(value.into_boxed_str())
    }
}

#[derive(Clone, Default, Debug, PartialEq, Eq, Hash, Serialize)]
pub struct Bytes(Vec<u8>);

impl SpanBytes for Bytes {
    fn from_static_bytes(value: &'static [u8]) -> Self {
        Self(value.to_vec())
    }
}

impl Borrow<[u8]> for Bytes {
    fn borrow(&self) -> &[u8] {
        &self.0
    }
}

#[derive(Clone, Default, Debug, PartialEq)]
pub struct PyTraceData;
impl TraceData for PyTraceData {
    type Text = PyBackedString;
    type Bytes = Bytes;
}
