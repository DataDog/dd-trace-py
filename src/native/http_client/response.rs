//! Python wrapper around `libdd_http_client::HttpResponse`.
//!
//! The body is held as `bytes::Bytes` (Arc-backed, cheap to clone). The
//! Python-side `PyBytes` view is built lazily and memoized in `PyOnceLock`
//! so repeated `resp.body()` calls don't reallocate.

use libdd_http_client::HttpResponse;
use pyo3::{prelude::*, sync::PyOnceLock, types::PyBytes};

#[pyclass(name = "HttpResponse", frozen)]
pub struct HttpResponsePy {
    #[pyo3(get)]
    status_code: u16,
    headers: Vec<(String, String)>,
    body: bytes::Bytes,
    // DEV: lazy `PyBytes` view — repeated `body()` calls reuse the same object.
    py_bytes: PyOnceLock<Py<PyBytes>>,
}

impl From<HttpResponse> for HttpResponsePy {
    fn from(r: HttpResponse) -> Self {
        Self {
            status_code: r.status_code(),
            headers: r.headers().to_vec(),
            body: r.body().clone(),
            py_bytes: PyOnceLock::new(),
        }
    }
}

#[pymethods]
impl HttpResponsePy {
    /// Response headers as `(name, value)` tuples. The list preserves
    /// insertion order and allows duplicate header names (e.g. multiple
    /// `Set-Cookie`).
    #[getter]
    fn headers(&self) -> Vec<(String, String)> {
        self.headers.clone()
    }

    /// Return the response body as `bytes`. Allocates a single `PyBytes`
    /// lazily and reuses it across calls.
    fn body<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        self.py_bytes
            .get_or_init(py, || PyBytes::new(py, &self.body).unbind())
            .bind(py)
            .clone()
    }

    /// Case-insensitive header lookup. Returns the first matching value.
    fn header(&self, name: &str) -> Option<String> {
        self.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.clone())
    }

    fn __repr__(&self) -> String {
        format!(
            "HttpResponse(status_code={}, body_len={})",
            self.status_code,
            self.body.len()
        )
    }
}
