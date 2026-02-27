use pyo3::{
    exceptions::PyKeyError,
    types::{PyAnyMethods as _, PyFloat, PyList, PyTuple},
    Bound, Py, PyAny, PyResult, Python,
};

use crate::py_string::PyBackedString;
use crate::span::SpanData;

/// Read-only Mapping view over a span's string attributes (`data.meta`).
///
/// Holds a `Py<SpanData>` reference and reads directly from the Rust HashMap on each
/// access — no PyDict is ever allocated. Key/value access via `PyBackedString.as_py()`
/// is a `Py_INCREF` of the stored Python string object (zero-copy).
///
/// `items()` returns a `PyList` of `(key, value)` tuples. CPython iterates lists via
/// `PyList_GET_ITEM` (pointer arithmetic), making `for k, v in meta.items():` fast.
#[pyo3::pyclass(
    name = "StrAttributesMapping",
    module = "ddtrace.internal._native",
    frozen
)]
pub struct StrAttributesMapping {
    pub(crate) parent: Py<SpanData>,
}

#[pyo3::pymethods]
impl StrAttributesMapping {
    fn __len__(&self, py: Python<'_>) -> usize {
        self.parent.borrow(py).data.meta.len()
    }

    fn __bool__(&self, py: Python<'_>) -> bool {
        !self.parent.borrow(py).data.meta.is_empty()
    }

    fn __contains__(&self, py: Python<'_>, key: Bound<'_, PyAny>) -> bool {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return false;
        };
        self.parent.borrow(py).data.meta.contains_key(&*key_bs)
    }

    fn __getitem__<'py>(
        &self,
        py: Python<'py>,
        key: Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return Err(PyKeyError::new_err(key.unbind()));
        };
        let borrowed = self.parent.borrow(py);
        match borrowed.data.meta.get(&*key_bs) {
            Some(v) => Ok(v.as_py(py)),
            None => Err(PyKeyError::new_err(key_bs.as_py(py).unbind())),
        }
    }

    #[pyo3(signature = (key, default=None))]
    fn get<'py>(
        &self,
        py: Python<'py>,
        key: Bound<'_, PyAny>,
        default: Option<Bound<'py, PyAny>>,
    ) -> Option<Bound<'py, PyAny>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return default;
        };
        let borrowed = self.parent.borrow(py);
        match borrowed.data.meta.get(&*key_bs) {
            Some(v) => Some(v.as_py(py)),
            None => default,
        }
    }

    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let borrowed = self.parent.borrow(py);
        let keys: Vec<Bound<'py, PyAny>> =
            borrowed.data.meta.keys().map(|k| k.as_py(py)).collect();
        drop(borrowed);
        let list = PyList::new(py, keys)?;
        list.as_any().try_iter().map(|i| i.into_any())
    }

    /// Returns a `PyList` of `(key, value)` tuples.
    ///
    /// Keys and values are near-zero-copy: `PyBackedString.as_py()` is a `Py_INCREF`
    /// of the stored Python string object. The list/tuple allocations are small for
    /// typical spans (5-20 entries).
    fn items<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let borrowed = self.parent.borrow(py);
        let items: Vec<Bound<'py, PyAny>> = borrowed
            .data
            .meta
            .iter()
            .map(|(k, v)| {
                PyTuple::new(py, [k.as_py(py), v.as_py(py)])
                    .map(|t| t.into_any())
                    .expect("PyTuple::new failed")
            })
            .collect();
        drop(borrowed);
        PyList::new(py, items)
    }

    fn keys<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let borrowed = self.parent.borrow(py);
        let keys: Vec<Bound<'py, PyAny>> =
            borrowed.data.meta.keys().map(|k| k.as_py(py)).collect();
        drop(borrowed);
        PyList::new(py, keys)
    }

    fn values<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let borrowed = self.parent.borrow(py);
        let vals: Vec<Bound<'py, PyAny>> =
            borrowed.data.meta.values().map(|v| v.as_py(py)).collect();
        drop(borrowed);
        PyList::new(py, vals)
    }
}

/// Read-only Mapping view over a span's numeric attributes (`data.metrics`).
///
/// Holds a `Py<SpanData>` reference and reads directly from the Rust HashMap on each
/// access — no PyDict is ever allocated. Values are `PyFloat` (a small, unavoidable
/// allocation since f64 has no Python singleton cache).
#[pyo3::pyclass(
    name = "NumericAttributesMapping",
    module = "ddtrace.internal._native",
    frozen
)]
pub struct NumericAttributesMapping {
    pub(crate) parent: Py<SpanData>,
}

#[pyo3::pymethods]
impl NumericAttributesMapping {
    fn __len__(&self, py: Python<'_>) -> usize {
        self.parent.borrow(py).data.metrics.len()
    }

    fn __bool__(&self, py: Python<'_>) -> bool {
        !self.parent.borrow(py).data.metrics.is_empty()
    }

    fn __contains__(&self, py: Python<'_>, key: Bound<'_, PyAny>) -> bool {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return false;
        };
        self.parent.borrow(py).data.metrics.contains_key(&*key_bs)
    }

    fn __getitem__<'py>(
        &self,
        py: Python<'py>,
        key: Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return Err(PyKeyError::new_err(key.unbind()));
        };
        let borrowed = self.parent.borrow(py);
        match borrowed.data.metrics.get(&*key_bs) {
            Some(&v) => Ok(PyFloat::new(py, v).into_any()),
            None => Err(PyKeyError::new_err(key_bs.as_py(py).unbind())),
        }
    }

    #[pyo3(signature = (key, default=None))]
    fn get<'py>(
        &self,
        py: Python<'py>,
        key: Bound<'_, PyAny>,
        default: Option<Bound<'py, PyAny>>,
    ) -> Option<Bound<'py, PyAny>> {
        let Ok(key_bs) = key.extract::<PyBackedString>() else {
            return default;
        };
        let borrowed = self.parent.borrow(py);
        match borrowed.data.metrics.get(&*key_bs) {
            Some(&v) => Some(PyFloat::new(py, v).into_any()),
            None => default,
        }
    }

    fn __iter__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let borrowed = self.parent.borrow(py);
        let keys: Vec<Bound<'py, PyAny>> =
            borrowed.data.metrics.keys().map(|k| k.as_py(py)).collect();
        drop(borrowed);
        let list = PyList::new(py, keys)?;
        list.as_any().try_iter().map(|i| i.into_any())
    }

    /// Returns a `PyList` of `(key, value)` tuples.
    ///
    /// Keys are near-zero-copy (`PyBackedString.as_py()` is a `Py_INCREF`).
    /// Values are `PyFloat` allocations (unavoidable for f64).
    fn items<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let borrowed = self.parent.borrow(py);
        let items: Vec<Bound<'py, PyAny>> = borrowed
            .data
            .metrics
            .iter()
            .map(|(k, &v)| {
                PyTuple::new(py, [k.as_py(py), PyFloat::new(py, v).into_any()])
                    .map(|t| t.into_any())
                    .expect("PyTuple::new failed")
            })
            .collect();
        drop(borrowed);
        PyList::new(py, items)
    }

    fn keys<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let borrowed = self.parent.borrow(py);
        let keys: Vec<Bound<'py, PyAny>> =
            borrowed.data.metrics.keys().map(|k| k.as_py(py)).collect();
        drop(borrowed);
        PyList::new(py, keys)
    }

    fn values<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let borrowed = self.parent.borrow(py);
        let vals: Vec<Bound<'py, PyAny>> = borrowed
            .data
            .metrics
            .values()
            .map(|&v| PyFloat::new(py, v).into_any())
            .collect();
        drop(borrowed);
        PyList::new(py, vals)
    }
}
