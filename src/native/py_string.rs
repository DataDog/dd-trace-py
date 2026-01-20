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
/// that can be put in a libdatadog span
pub struct PyBackedString {
    data: ptr::NonNull<str>,
    #[allow(unused)]
    storage: Option<Py<PyAny>>,
}

impl PyBackedString {
    pub fn clone_ref<'py>(&self, py: Python<'py>) -> Self {
        Self {
            data: self.data,
            storage: self.storage.as_ref().map(|s| s.clone_ref(py)),
        }
    }

    pub fn py_none<'py>(py: Python<'py>) -> Self {
        Self {
            data: unsafe { ptr::NonNull::new_unchecked("" as *const str as *mut _) },
            storage: Some(py.None()),
        }
    }

    pub fn from_string<'py>(py: Python<'py>, s: &str) -> Self {
        let s = PyString::new(py, s);
        // SAFETY: s is valid UTF-8 so the conversion should normally never fail
        Self::try_from(s).unwrap()
    }
}

impl pyo3::FromPyObject<'_> for PyBackedString {
    fn extract_bound(obj: &pyo3::Bound<'_, PyAny>) -> pyo3::PyResult<Self> {
        if let Ok(py_string) = obj.downcast::<pyo3::types::PyString>() {
            return Self::try_from(py_string.to_owned());
        }
        if let Ok(py_bytes) = obj.downcast::<pyo3::types::PyBytes>() {
            return Self::try_from(py_bytes.to_owned());
        }
        if let Ok(py_none) = obj.downcast_exact::<pyo3::types::PyNone>() {
            return Ok(Self::from(py_none.to_owned()));
        }
        Err(PyErr::new::<PyValueError, _>(
            "argument needs to be either a 'str', uft8 encoded 'bytes', or 'None'",
        ))
    }
}

impl<'py> pyo3::IntoPyObject<'py> for &PyBackedString {
    type Target = pyo3::types::PyAny;

    type Output = pyo3::Bound<'py, Self::Target>;

    type Error = std::convert::Infallible;

    fn into_pyobject(self, py: pyo3::Python<'py>) -> Result<Self::Output, Self::Error> {
        Ok(match &self.storage {
            Some(python_str) => python_str.bind(py).to_owned(),
            None => PyString::new(py, self.deref()).into_any(),
        })
    }
}

// Py bytes as immutable and can thus be safely shared between threads
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
            data: unsafe { NonNull::new_unchecked("" as *const str as *mut _) },
            storage: Some(value.to_owned().unbind().into_any()),
        }
    }
}

impl std::ops::Deref for PyBackedString {
    type Target = str;

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
            data: unsafe { ptr::NonNull::new_unchecked(value as *const str as *mut _) },
            storage: None,
        }
    }
}
