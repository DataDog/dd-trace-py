//! Python wrapper around `libdd_http_client::HttpResponse`.
//!
//! DEV: The wrapper holds the libdd `HttpResponse` by value and reads through
//! its borrowing accessors on demand — it does NOT decompose it into owned
//! fields. This avoids a per-response deep copy of the header `Vec` (paid even
//! when the caller only reads `status_code`/`body()`, as RemoteConfig,
//! telemetry, and `agent.info` do). The success-path body stays an Arc-backed
//! `bytes::Bytes` inside the response; the Python-side `PyBytes` view is built
//! lazily and memoized in a `std::sync::OnceLock` so repeated `body()` calls
//! don't reallocate.
//!
//! DEV: `OnceLock<Py<PyBytes>>` (std, not `PyOnceLock`) is used so the cached
//! reference can be read in `__traverse__` without a `Python` token (which the
//! GC protocol does not provide). `body()` always holds the GIL when it
//! initializes the cell, and the GIL serializes those calls, so the std cell
//! is sound here.

use libdd_http_client::HttpResponse;
use std::sync::OnceLock;

use pyo3::{prelude::*, types::PyBytes};

#[pyclass(name = "HttpResponse", frozen)]
pub struct HttpResponsePy {
    inner: HttpResponse,
    // DEV: lazy `PyBytes` view — repeated `body()` calls reuse the same object.
    py_bytes: OnceLock<Py<PyBytes>>,
}

impl From<HttpResponse> for HttpResponsePy {
    fn from(inner: HttpResponse) -> Self {
        // DEV: pure move — no headers/body copy at the FFI boundary.
        Self {
            inner,
            py_bytes: OnceLock::new(),
        }
    }
}

#[pymethods]
impl HttpResponsePy {
    #[getter]
    fn status_code(&self) -> u16 {
        self.inner.status_code()
    }

    /// Response headers as `(name, value)` tuples. The list preserves
    /// insertion order and allows duplicate header names (e.g. multiple
    /// `Set-Cookie`).
    #[getter]
    fn headers(&self) -> Vec<(&str, &str)> {
        // DEV: borrow rather than clone — PyO3 materializes owned Python
        // strings from the &str directly, so the only header copy is the
        // unavoidable one into the Python objects.
        self.inner
            .headers()
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect()
    }

    /// Return the response body as `bytes`. Allocates a single `PyBytes`
    /// lazily and reuses it across calls.
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

    /// Cyclic-GC traversal. `HttpResponse` itself holds no Python references;
    /// the only one is the lazily-built `py_bytes` cache. `bytes` is an atomic
    /// leaf and cannot form a cycle, but PyO3 requires every `#[pyclass]` that
    /// holds a `Py<...>` to implement `__traverse__` for correct refcount
    /// accounting (see `.sg/rules/pyclass-missing-traverse.yml`). Frozen, so no
    /// `__clear__` is needed.
    fn __traverse__(&self, visit: pyo3::PyVisit<'_>) -> Result<(), pyo3::PyTraverseError> {
        if let Some(b) = self.py_bytes.get() {
            visit.call(b)?;
        }
        Ok(())
    }
}
