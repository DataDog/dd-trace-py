//! Python wrapper around `libdd_http_client::HttpResponse`.
//!
//! DEV: The wrapper holds the libdd `HttpResponse` by value and reads through
//! its borrowing accessors on demand — it does NOT decompose it into owned
//! fields. This avoids a per-response deep copy of the header `Vec` (paid even
//! when the caller only reads `status_code`/`body()`, as RemoteConfig,
//! telemetry, and `agent.info` do). The success-path body stays an Arc-backed
//! `bytes::Bytes` inside the response; the Python-side `PyBytes` view is built
//! lazily and memoized in `PyOnceLock` so repeated `body()` calls don't
//! reallocate.

use libdd_http_client::HttpResponse;
use pyo3::{prelude::*, sync::PyOnceLock, types::PyBytes};

#[pyclass(name = "HttpResponse", frozen)]
pub struct HttpResponsePy {
    inner: HttpResponse,
    // DEV: lazy `PyBytes` view — repeated `body()` calls reuse the same object.
    py_bytes: PyOnceLock<Py<PyBytes>>,
}

impl From<HttpResponse> for HttpResponsePy {
    fn from(inner: HttpResponse) -> Self {
        // DEV: pure move — no headers/body copy at the FFI boundary.
        Self {
            inner,
            py_bytes: PyOnceLock::new(),
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
            .get_or_init(py, || PyBytes::new(py, self.inner.body()).unbind())
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
}
