//! Python wrapper around `libdd_http_client::HttpResponse`.
//!
//! DEV: The wrapper holds the libdd `HttpResponse` by value and reads through
//! its borrowing accessors on demand — it does NOT decompose it into owned
//! fields. This avoids a per-response deep copy of the header `Vec` when only
//! `status_code` or `body()` is read. The success-path body stays an Arc-backed
//! `bytes::Bytes` inside the response; the Python-side `PyBytes` and `PyList`
//! views are built lazily and memoized so repeated calls don't reallocate.

use libdd_http_client::HttpResponse;
use std::sync::OnceLock;

use pyo3::{
    prelude::*,
    types::{PyBytes, PyList},
};

#[pyclass(name = "HttpResponse", frozen)]
pub struct HttpResponsePy {
    inner: HttpResponse,
    // DEV: lazy Python views — built once on first access, reused on every
    // subsequent call (refcount bump only).
    py_bytes: OnceLock<Py<PyBytes>>,
    py_headers: OnceLock<Py<PyList>>,
}

impl From<HttpResponse> for HttpResponsePy {
    fn from(inner: HttpResponse) -> Self {
        // DEV: pure move — no headers/body copy at the FFI boundary.
        Self {
            inner,
            py_bytes: OnceLock::new(),
            py_headers: OnceLock::new(),
        }
    }
}

#[pymethods]
impl HttpResponsePy {
    #[getter]
    fn status_code(&self) -> u16 {
        self.inner.status_code()
    }

    /// Response headers as a list of `(name, value)` tuples. The list preserves
    /// insertion order and allows duplicate header names (e.g. multiple
    /// `Set-Cookie`). Builds the Python list once and reuses it across calls.
    #[getter]
    fn headers<'py>(&self, py: Python<'py>) -> Bound<'py, PyList> {
        self.py_headers
            .get_or_init(|| {
                PyList::new(
                    py,
                    self.inner
                        .headers()
                        .iter()
                        .map(|(k, v)| (k.as_str(), v.as_str())),
                )
                .expect("header strings must be valid UTF-8")
                .unbind()
            })
            .bind(py)
            .clone()
    }

    /// Return the response body as `bytes`. Builds the Python object once and
    /// reuses it across calls.
    fn body<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        self.py_bytes
            .get_or_init(|| PyBytes::new(py, self.inner.body()).unbind())
            .bind(py)
            .clone()
    }

    /// Case-insensitive header lookup. Returns the first matching value.
    fn header(&self, name: &str) -> Option<&str> {
        self.inner
            .headers()
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }

    fn __repr__(&self) -> String {
        format!(
            "HttpResponse(status_code={}, body_len={})",
            self.inner.status_code(),
            self.inner.body().len()
        )
    }

    /// Cyclic-GC traversal. Neither `bytes` nor header strings can form cycles,
    /// but PyO3 requires every `#[pyclass]` holding `Py<…>` to implement
    /// `__traverse__` for correct refcount accounting. Frozen, so no
    /// `__clear__` is needed.
    ///
    /// DEV: the caches use `std::sync::OnceLock<Py<…>>` rather than
    /// `pyo3::sync::PyOnceLock` because `__traverse__` is called without a
    /// `Python` token (the GC protocol does not provide one). `OnceLock::get`
    /// needs no token; all initialization paths hold the GIL, so the cells
    /// are sound.
    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        if let Some(b) = self.py_bytes.get() {
            visit.call(b)?;
        }
        if let Some(h) = self.py_headers.get() {
            visit.call(h)?;
        }
        Ok(())
    }
}
