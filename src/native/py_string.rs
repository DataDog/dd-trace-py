use std::{
    ops::Deref as _,
    ptr::{self, NonNull},
};

use pyo3::{
    exceptions::PyValueError,
    types::{PyAnyMethods as _, PyBytesMethods as _, PyNone, PyString, PyStringMethods as _},
    Py, PyAny, PyErr, Python,
};

use libdd_trace_utils::span::SpanText;

/// A Python bytes/str backed utf-8 string we can read without needing access to the GIL
/// that can be put in a libdatadog span.
///
/// ## Storage Semantics
///
/// The `storage` field has the following semantics:
/// - `Some(py_obj)` where `py_obj` is a `PyString` or `PyBytes`: Holds the Python string/bytes object
/// - `Some(py_obj)` where `py_obj` is `PyNone`: Represents Python `None` (empty string for Rust)
/// - `None`: Static Rust string (from `from_static_str`/`Default`), no Python object to keep alive
///
/// When converting back to Python via `IntoPyObject`:
/// - `Some(py_obj)` returns the stored Python object (zero-copy)
/// - `None` creates an interned Python string (cached by Python for repeated access)
pub struct PyBackedString {
    /// memory view over the python object bytes, or static data
    data: ptr::NonNull<str>,
    /// Prevents the underlying Python object from being garbage collected.
    /// See Storage Semantics above for details.
    storage: Option<Py<PyAny>>,
}

impl PyBackedString {
    pub fn clone_ref<'py>(&self, py: Python<'py>) -> Self {
        Self {
            data: self.data,
            storage: self.storage.as_ref().map(|s| s.clone_ref(py)),
        }
    }

    /// Check if this `PyBackedString` represents Python `None` (not just an empty string).
    ///
    /// Returns `true` only if the storage contains Python's `None` object.
    /// Returns `false` for empty strings created from static data or Python empty strings.
    #[inline(always)]
    pub fn is_py_none(&self, py: Python<'_>) -> bool {
        self.storage.as_ref().is_some_and(|obj| obj.is_none(py))
    }

    pub fn py_none<'py>(py: Python<'py>) -> Self {
        Self {
            // SAFETY: "" is a non-null 'static str literal
            data: unsafe { ptr::NonNull::new_unchecked("" as *const str as *mut _) },
            storage: Some(py.None()),
        }
    }

    /// Get the underlying Python object directly for zero-copy semantics.
    ///
    /// Returns the stored Python object if available (PyString, PyBytes, or PyNone),
    /// or creates an interned Python string for static strings.
    #[inline(always)]
    pub fn as_py<'py>(&self, py: Python<'py>) -> pyo3::Bound<'py, PyAny> {
        match &self.storage {
            Some(obj) => obj.bind(py).clone(),
            None => PyString::intern(py, self.deref()).into_any(),
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
// 2. The `storage` field keeps the Python object alive, preventing deallocation
// 3. For PyString, `to_str()` returns a pointer to either:
//    - The compact ASCII buffer (for ASCII-only strings), or
//    - A UTF-8 cache that's lazily created and stored on the object
//    Both are stable for the lifetime of the PyUnicode object.
// 4. For PyBytes, the internal buffer is immutable and stable.
unsafe impl Sync for PyBackedString {}
unsafe impl Send for PyBackedString {}

impl TryFrom<pyo3::Bound<'_, pyo3::types::PyString>> for PyBackedString {
    type Error = pyo3::PyErr;
    fn try_from(py_string: pyo3::Bound<'_, pyo3::types::PyString>) -> Result<Self, Self::Error> {
        let s = py_string.to_str()?;
        let data = ptr::NonNull::from(s);
        Ok(Self {
            storage: Some(py_string.unbind().into_any()),
            data,
        })
    }
}

impl TryFrom<pyo3::Bound<'_, pyo3::types::PyBytes>> for PyBackedString {
    type Error = pyo3::PyErr;
    fn try_from(py_bytes: pyo3::Bound<'_, pyo3::types::PyBytes>) -> Result<Self, Self::Error> {
        let s = std::str::from_utf8(py_bytes.as_bytes())
            .map_err(|_e| pyo3::PyErr::new::<PyValueError, _>("'bytes' are not utf8 encoded"))?;
        let data = ptr::NonNull::from(s);
        Ok(Self {
            storage: Some(py_bytes.unbind().into_any()),
            data,
        })
    }
}

impl From<pyo3::Bound<'_, PyNone>> for PyBackedString {
    fn from(value: pyo3::Bound<'_, PyNone>) -> Self {
        Self {
            // SAFETY: "" is a non-null 'static str literal
            data: unsafe { NonNull::new_unchecked("" as *const str as *mut _) },
            storage: Some(value.to_owned().unbind().into_any()),
        }
    }
}

impl std::ops::Deref for PyBackedString {
    type Target = str;

    #[inline]
    fn deref(&self) -> &Self::Target {
        unsafe { self.data.as_ref() }
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
            // SAFETY: value is a 'static str reference, guaranteed to be non-null
            data: unsafe { ptr::NonNull::new_unchecked(value as *const str as *mut _) },
            storage: None,
        }
    }
}
