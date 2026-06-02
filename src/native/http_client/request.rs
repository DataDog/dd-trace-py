//! Python wrappers around `libdd_http_client::HttpRequest`, `HttpMethod`, and
//! `MultipartPart`.
//!
//! DEV: `PyBackedBytes` is `AsRef<[u8]> + Send + 'static`, so
//! `bytes::Bytes::from_owner(pybacked)` wraps the underlying Python `bytes`
//! buffer **without copying** (requires `bytes = "1.10"`). This is the same
//! pattern as `TraceExporterPy::send`.
//!
//! `HttpRequestPy` stores the wrapped request as `Option<HttpRequest>` so each
//! chained `with_*` call mutates in place — O(1) per call, not O(N²) clones.

use libdd_http_client::{HttpMethod, HttpRequest, MultipartPart};
use pyo3::{exceptions::PyValueError, prelude::*, pybacked::PyBackedBytes};
use std::time::Duration;

pub(crate) const REQUEST_CONSUMED_MSG: &str =
    "HttpRequest has already been consumed (was send() called?)";

/// Mirror of libdd's `HttpMethod` enum, exposed as a Python `IntEnum` via
/// `pyclass(eq, eq_int)`. Includes `Options` even though the convenience
/// methods on `HttpClient` do not — `HttpRequest(HttpMethod.Options, ...)`
/// still works through `client.send(req)`.
#[pyclass(name = "HttpMethod", eq, eq_int, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum HttpMethodPy {
    Get,
    Post,
    Put,
    Delete,
    Head,
    Patch,
    Options,
}

impl From<HttpMethodPy> for HttpMethod {
    fn from(m: HttpMethodPy) -> Self {
        match m {
            HttpMethodPy::Get => HttpMethod::Get,
            HttpMethodPy::Post => HttpMethod::Post,
            HttpMethodPy::Put => HttpMethod::Put,
            HttpMethodPy::Delete => HttpMethod::Delete,
            HttpMethodPy::Head => HttpMethod::Head,
            HttpMethodPy::Patch => HttpMethod::Patch,
            HttpMethodPy::Options => HttpMethod::Options,
        }
    }
}

impl From<HttpMethod> for HttpMethodPy {
    fn from(m: HttpMethod) -> Self {
        match m {
            HttpMethod::Get => HttpMethodPy::Get,
            HttpMethod::Post => HttpMethodPy::Post,
            HttpMethod::Put => HttpMethodPy::Put,
            HttpMethod::Delete => HttpMethodPy::Delete,
            HttpMethod::Head => HttpMethodPy::Head,
            HttpMethod::Patch => HttpMethodPy::Patch,
            HttpMethod::Options => HttpMethodPy::Options,
        }
    }
}

/// A single multipart/form-data part. The `data` payload is wrapped zero-copy
/// via `bytes::Bytes::from_owner`.
#[pyclass(name = "MultipartPart")]
pub struct MultipartPartPy {
    pub(crate) inner: MultipartPart,
}

#[pymethods]
impl MultipartPartPy {
    #[new]
    #[pyo3(signature = (name, data, *, filename=None, content_type=None))]
    fn new(
        name: String,
        data: PyBackedBytes,
        filename: Option<String>,
        content_type: Option<String>,
    ) -> Self {
        let mut part = MultipartPart::new(name, bytes::Bytes::from_owner(data));
        if let Some(f) = filename {
            part = part.with_filename(f);
        }
        if let Some(ct) = content_type {
            part = part.with_content_type(ct);
        }
        Self { inner: part }
    }
}

/// Fluent HTTP request builder.
///
/// Stored as `Option<HttpRequest>` so `client.send(req)` can `take()` the
/// inner value out of the Python wrapper without cloning. After `send`, the
/// wrapper is empty — subsequent `with_*` / getter calls raise `ValueError`.
#[pyclass(name = "HttpRequest")]
pub struct HttpRequestPy {
    inner: Option<HttpRequest>,
}

impl HttpRequestPy {
    fn try_as_mut(&mut self) -> PyResult<&mut HttpRequest> {
        self.inner
            .as_mut()
            .ok_or_else(|| PyValueError::new_err(REQUEST_CONSUMED_MSG))
    }

    fn try_as_ref(&self) -> PyResult<&HttpRequest> {
        self.inner
            .as_ref()
            .ok_or_else(|| PyValueError::new_err(REQUEST_CONSUMED_MSG))
    }

    pub(crate) fn take_inner(&mut self) -> PyResult<HttpRequest> {
        self.inner
            .take()
            .ok_or_else(|| PyValueError::new_err(REQUEST_CONSUMED_MSG))
    }

    /// Shared constructor used by the convenience HTTP methods on
    /// `HttpClientPy` (get/post/put/...). Not exposed to Python.
    pub(crate) fn build_inner(
        method: HttpMethodPy,
        url: String,
        headers: Option<Vec<(String, String)>>,
        body: Option<PyBackedBytes>,
        timeout_ms: Option<u64>,
    ) -> HttpRequest {
        let mut req = HttpRequest::new(method.into(), url);
        if let Some(hs) = headers {
            req.headers_mut().extend(hs);
        }
        if let Some(b) = body {
            *req.body_mut() = bytes::Bytes::from_owner(b);
        }
        if let Some(ms) = timeout_ms {
            req = req.with_timeout(Duration::from_millis(ms));
        }
        req
    }
}

#[pymethods]
impl HttpRequestPy {
    #[new]
    fn new(method: HttpMethodPy, url: String) -> Self {
        Self {
            inner: Some(HttpRequest::new(method.into(), url)),
        }
    }

    /// Append a header. Chainable.
    fn with_header(mut slf: PyRefMut<'_, Self>, name: String, value: String) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.headers_mut().push((name, value));
        Ok(slf.into())
    }

    /// Append multiple headers at once. Chainable.
    fn with_headers(
        mut slf: PyRefMut<'_, Self>,
        headers: Vec<(String, String)>,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?.headers_mut().extend(headers);
        Ok(slf.into())
    }

    /// Set the request body. Chainable.
    fn with_body(mut slf: PyRefMut<'_, Self>, body: PyBackedBytes) -> PyResult<Py<Self>> {
        *slf.try_as_mut()?.body_mut() = bytes::Bytes::from_owner(body);
        Ok(slf.into())
    }

    /// Set a per-request timeout, overriding the client default. Chainable.
    fn with_timeout_ms(mut slf: PyRefMut<'_, Self>, ms: u64) -> PyResult<Py<Self>> {
        // DEV: libdd has no `timeout_mut()`; reuse the take + reassign pattern.
        let r = slf.take_inner()?;
        slf.inner = Some(r.with_timeout(Duration::from_millis(ms)));
        Ok(slf.into())
    }

    /// Append a multipart part. Chainable.
    ///
    /// DEV: Setting both `with_body(...)` and `with_multipart_part(...)` is
    /// rejected at `send()` time, not here. The libdd `MultipartPart` is
    /// `Clone`, so we clone the inner value out of the Python wrapper.
    fn with_multipart_part(
        mut slf: PyRefMut<'_, Self>,
        part: &MultipartPartPy,
    ) -> PyResult<Py<Self>> {
        slf.try_as_mut()?
            .multipart_parts_mut()
            .push(part.inner.clone());
        Ok(slf.into())
    }

    #[getter]
    fn method(&self) -> PyResult<HttpMethodPy> {
        Ok(self.try_as_ref()?.method().into())
    }

    #[getter]
    fn url(&self) -> PyResult<&str> {
        Ok(self.try_as_ref()?.url())
    }

    #[getter]
    fn headers(&self) -> PyResult<Vec<(&str, &str)>> {
        // DEV: borrow rather than .to_vec() — PyO3 builds the owned Python
        // strings from the &str directly (no redundant Rust-side copy).
        Ok(self
            .try_as_ref()?
            .headers()
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect())
    }

    fn __repr__(&self) -> String {
        match &self.inner {
            Some(req) => format!(
                "HttpRequest(method={:?}, url={:?})",
                req.method(),
                req.url()
            ),
            None => "HttpRequest(<consumed>)".to_owned(),
        }
    }
}
