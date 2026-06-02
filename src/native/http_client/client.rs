//! Python wrappers around `libdd_http_client::HttpClient` and
//! `HttpClientBuilder`.
//!
//! Each `send()` runs `runtime.block_on(inner.send(req))` inside
//! `py.detach`, releasing the GIL for the duration of the I/O. The libdd
//! result is mapped to a Python exception **after** `py.detach` returns —
//! the GIL is already held by PyO3's `#[pymethods]` wrapper, so we never
//! need to `Python::with_gil` reacquire.

use libdd_http_client::{HttpClient, HttpClientBuilder, HttpClientError, HttpRequest, RetryConfig};
use libdd_shared_runtime::SharedRuntime;
use pyo3::{exceptions::PyValueError, prelude::*, pybacked::PyBackedBytes};
use std::{path::PathBuf, sync::Arc, time::Duration};

use crate::http_client::{
    errors::http_error_to_pyerr,
    request::{HttpMethodPy, HttpRequestPy},
    response::HttpResponsePy,
};
use crate::shared_runtime::SharedRuntimePy;

const CLIENT_CLOSED_MSG: &str = "HttpClient has already been shut down";
const BUILDER_CONSUMED_MSG: &str = "HttpClientBuilder has already been consumed";

/// A pooled HTTP client wrapping `libdd_http_client::HttpClient`.
///
/// Holds the libdd client and its tokio runtime side-by-side (in a single
/// `Option` so both are released together on shutdown). The runtime is the
/// `SharedRuntime` supplied at `build` time — same pattern as
/// `TraceExporter`.
#[pyclass(name = "HttpClient")]
pub struct HttpClientPy {
    // DEV: client + runtime are collapsed into one Option so both are freed
    // together on shutdown / Drop.
    inner: Option<(HttpClient, Arc<SharedRuntime>)>,
}

impl HttpClientPy {
    /// Shared implementation for `send` and the convenience methods. Takes
    /// ownership of a `HttpRequest`, runs it on the runtime with the GIL
    /// released, then maps the libdd error to a Python exception inside the
    /// GIL.
    fn run_send(&self, py: Python<'_>, req: HttpRequest) -> PyResult<HttpResponsePy> {
        let (client, runtime) = self
            .inner
            .as_ref()
            .ok_or_else(|| PyValueError::new_err(CLIENT_CLOSED_MSG))?;
        let runtime = runtime.clone();
        // DEV: `SharedRuntime::block_on` returns `Result<F::Output, io::Error>`,
        // where the inner is `Result<HttpResponse, HttpClientError>`. The
        // io::Error wrapping is only used if the runtime fails to construct a
        // fallback runtime (extremely unlikely outside of fork chaos); map it
        // to HttpIoError just in case so callers never see a raw PyResult err
        // type leak through.
        let result = py.detach(move || runtime.block_on(client.send(req)));
        match result {
            Ok(Ok(resp)) => Ok(HttpResponsePy::from(resp)),
            Ok(Err(e)) => Err(http_error_to_pyerr(py, e)),
            Err(io_err) => Err(http_error_to_pyerr(
                py,
                HttpClientError::IoError(format!("shared runtime block_on failed: {io_err}")),
            )),
        }
    }
}

#[pymethods]
impl HttpClientPy {
    /// Send a fully-built `HttpRequest`. Consumes the request — subsequent
    /// operations on the same `HttpRequestPy` raise `ValueError`.
    ///
    /// Releases the GIL for the duration of the I/O.
    fn send(
        &self,
        py: Python<'_>,
        mut request: PyRefMut<'_, HttpRequestPy>,
    ) -> PyResult<HttpResponsePy> {
        let req = request.take_inner()?;
        self.run_send(py, req)
    }

    /// Convenience GET. Headers must be an iterable of ``(name, value)``
    /// tuples.
    #[pyo3(signature = (url, *, headers=None, timeout_ms=None))]
    fn get(
        &self,
        py: Python<'_>,
        url: String,
        headers: Option<Vec<(String, String)>>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        let req = HttpRequestPy::build_inner(HttpMethodPy::Get, url, headers, None, timeout_ms);
        self.run_send(py, req)
    }

    /// Convenience HEAD.
    #[pyo3(signature = (url, *, headers=None, timeout_ms=None))]
    fn head(
        &self,
        py: Python<'_>,
        url: String,
        headers: Option<Vec<(String, String)>>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        let req = HttpRequestPy::build_inner(HttpMethodPy::Head, url, headers, None, timeout_ms);
        self.run_send(py, req)
    }

    /// Convenience DELETE.
    #[pyo3(signature = (url, *, headers=None, timeout_ms=None))]
    fn delete(
        &self,
        py: Python<'_>,
        url: String,
        headers: Option<Vec<(String, String)>>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        let req = HttpRequestPy::build_inner(HttpMethodPy::Delete, url, headers, None, timeout_ms);
        self.run_send(py, req)
    }

    /// Convenience POST.
    #[pyo3(signature = (url, *, headers=None, body=None, timeout_ms=None))]
    fn post(
        &self,
        py: Python<'_>,
        url: String,
        headers: Option<Vec<(String, String)>>,
        body: Option<PyBackedBytes>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        let req = HttpRequestPy::build_inner(HttpMethodPy::Post, url, headers, body, timeout_ms);
        self.run_send(py, req)
    }

    /// Convenience PUT.
    #[pyo3(signature = (url, *, headers=None, body=None, timeout_ms=None))]
    fn put(
        &self,
        py: Python<'_>,
        url: String,
        headers: Option<Vec<(String, String)>>,
        body: Option<PyBackedBytes>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        let req = HttpRequestPy::build_inner(HttpMethodPy::Put, url, headers, body, timeout_ms);
        self.run_send(py, req)
    }

    /// Convenience PATCH.
    #[pyo3(signature = (url, *, headers=None, body=None, timeout_ms=None))]
    fn patch(
        &self,
        py: Python<'_>,
        url: String,
        headers: Option<Vec<(String, String)>>,
        body: Option<PyBackedBytes>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        let req = HttpRequestPy::build_inner(HttpMethodPy::Patch, url, headers, body, timeout_ms);
        self.run_send(py, req)
    }

    /// Explicit shutdown. After this, `send`/`get`/`post`/... raise
    /// `ValueError`. Optional — `Drop` runs the same close path.
    fn shutdown(&mut self) {
        drop(self.inner.take());
    }

    fn __repr__(&self) -> &'static str {
        if self.inner.is_some() {
            "HttpClient(<open>)"
        } else {
            "HttpClient(<closed>)"
        }
    }
}

impl Drop for HttpClientPy {
    fn drop(&mut self) {
        // DEV: reqwest's pool cleans up its background tasks synchronously on
        // the runtime, no explicit timeout needed (TraceExporter has one
        // because of its own worker threads; HttpClient has none of its own).
        drop(self.inner.take());
    }
}

/// Builder for `HttpClient`. Each setter takes ownership of the inner libdd
/// builder (which has `mut self -> Self` setters), runs the setter, and
/// stores the new builder back. Chainable.
#[pyclass(name = "HttpClientBuilder")]
pub struct HttpClientBuilderPy {
    builder: Option<HttpClientBuilder>,
}

impl HttpClientBuilderPy {
    fn take_builder(&mut self) -> PyResult<HttpClientBuilder> {
        self.builder
            .take()
            .ok_or_else(|| PyValueError::new_err(BUILDER_CONSUMED_MSG))
    }
}

#[pymethods]
impl HttpClientBuilderPy {
    #[new]
    fn new() -> Self {
        // DEV: libdd's `HttpClientBuilder.build()` requires `base_url` to have
        // been set or returns `InvalidConfig`. `base_url` is informational —
        // the actual request URL is on `HttpRequest::new(method, url)` — so we
        // pre-set an empty string here. Callers do not interact with this.
        Self {
            builder: Some(HttpClient::builder().base_url(String::new())),
        }
    }

    /// Set the default request timeout. Chainable.
    fn set_timeout_ms(mut slf: PyRefMut<'_, Self>, ms: u64) -> PyResult<Py<Self>> {
        let b = slf.take_builder()?;
        slf.builder = Some(b.timeout(Duration::from_millis(ms)));
        Ok(slf.into())
    }

    /// Configure whether HTTP 4xx/5xx responses are surfaced as errors.
    /// Default ``True``.
    fn set_treat_http_errors_as_errors(
        mut slf: PyRefMut<'_, Self>,
        value: bool,
    ) -> PyResult<Py<Self>> {
        let b = slf.take_builder()?;
        slf.builder = Some(b.treat_http_errors_as_errors(value));
        Ok(slf.into())
    }

    /// Allow or disallow connection pooling. Default ``True``.
    fn set_allow_connection_pooling(
        mut slf: PyRefMut<'_, Self>,
        allow: bool,
    ) -> PyResult<Py<Self>> {
        let b = slf.take_builder()?;
        slf.builder = Some(b.allow_connection_pooling(allow));
        Ok(slf.into())
    }

    /// Enable automatic retries with exponential backoff. `max_retries=0` is
    /// equivalent to no retry — libdd skips the retry loop entirely in that
    /// case.
    #[pyo3(signature = (max_retries, initial_delay_ms=100, jitter=true))]
    fn set_retry(
        mut slf: PyRefMut<'_, Self>,
        max_retries: u32,
        initial_delay_ms: u64,
        jitter: bool,
    ) -> PyResult<Py<Self>> {
        let b = slf.take_builder()?;
        let cfg = RetryConfig::new()
            .max_retries(max_retries)
            .initial_delay(Duration::from_millis(initial_delay_ms))
            .with_jitter(jitter);
        slf.builder = Some(b.retry(cfg));
        Ok(slf.into())
    }

    /// Route requests through a Unix Domain Socket. The host portion of each
    /// `HttpRequest` URL is ignored when this is set.
    #[cfg(unix)]
    fn set_unix_socket(mut slf: PyRefMut<'_, Self>, path: String) -> PyResult<Py<Self>> {
        let b = slf.take_builder()?;
        slf.builder = Some(b.unix_socket(PathBuf::from(path)));
        Ok(slf.into())
    }

    #[cfg(not(unix))]
    fn set_unix_socket(_slf: PyRefMut<'_, Self>, _path: String) -> PyResult<Py<Self>> {
        Err(PyValueError::new_err(
            "Unix sockets are not supported on this platform",
        ))
    }

    /// Consume the builder and return a configured `HttpClient`. The
    /// `shared_runtime` is the process-wide `NativeRuntime` singleton from
    /// `ddtrace.internal.native_runtime.get_native_runtime()`.
    fn build(
        &mut self,
        py: Python<'_>,
        shared_runtime: PyRef<'_, SharedRuntimePy>,
    ) -> PyResult<HttpClientPy> {
        let runtime = shared_runtime.as_arc().clone();
        let builder = self.take_builder()?;
        let inner = builder.build().map_err(|e| http_error_to_pyerr(py, e))?;
        Ok(HttpClientPy {
            inner: Some((inner, runtime)),
        })
    }
}
