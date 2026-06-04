//! The `HTTPClient` PyO3 class — the single public entry point for native HTTP.
//!
//! This is an *ergonomic* client: it owns a base URL, default headers, and the
//! shared tokio runtime, and exposes `get`/`post`/... taking a relative path.
//! The low-level `libdd_http_client` builder/request types are consumed here in
//! Rust and never cross into Python — Python sees only this class, `HttpResponse`,
//! and the error hierarchy.
//!
//! Each request runs `runtime.block_on(client.send(req))` inside `py.detach`,
//! releasing the GIL for the duration of the I/O. The libdd result is mapped to a
//! Python exception **after** `py.detach` returns (the GIL is already held by
//! PyO3's `#[pymethods]` wrapper), so no `Python::with_gil` reacquire is needed.
//!
//! DEV: the runtime is passed explicitly to `__new__` rather than fetched here so
//! the native crate never has to reach back into Python for the process-wide
//! singleton.

use libdd_http_client::{HttpClient, HttpClientError, HttpMethod, HttpRequest, RetryConfig};
use libdd_shared_runtime::SharedRuntime;
use pyo3::{exceptions::PyValueError, prelude::*, pybacked::PyBackedBytes};
use std::{sync::Arc, time::Duration};
use url::Url;

use crate::http_client::{errors::http_error_to_pyerr, response::HttpResponsePy};
use crate::shared_runtime::SharedRuntimePy;

const CLIENT_CLOSED_MSG: &str = "HTTPClient has already been shut down";

/// A pooled, base-URL HTTP client over `libdd_http_client::HttpClient`.
///
/// `subclass` so the Python `ddtrace.internal.http_client.HTTPClient` can extend
/// it to inject the shared runtime.
#[pyclass(name = "HTTPClient", subclass)]
pub struct HttpClientPy {
    // DEV: client + runtime collapsed into one Option so both are freed together
    // on shutdown / Drop.
    inner: Option<(HttpClient, Arc<SharedRuntime>)>,
    // Request origin that relative paths are joined onto (e.g.
    // "http://localhost:8126"; "http://localhost" for UDS). No trailing slash.
    base: String,
    default_headers: Vec<(String, String)>,
}

impl HttpClientPy {
    /// Join the base origin with a request path.
    fn full_url(&self, path: &str) -> String {
        if path.starts_with('/') {
            format!("{}{}", self.base, path)
        } else {
            format!("{}/{}", self.base, path)
        }
    }

    /// Build the libdd request, run it on the runtime with the GIL released, and
    /// map the result to a Python response or exception.
    fn request(
        &self,
        py: Python<'_>,
        method: HttpMethod,
        path: &str,
        headers: Option<Vec<(String, String)>>,
        body: Option<PyBackedBytes>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        let (client, runtime) = self
            .inner
            .as_ref()
            .ok_or_else(|| PyValueError::new_err(CLIENT_CLOSED_MSG))?;
        let runtime = runtime.clone();

        let mut req = HttpRequest::new(method, self.full_url(path));
        // Populate headers directly into the request without an intermediate Vec:
        // push defaults first, then per-request overrides (last writer wins by name).
        req.headers_mut()
            .extend(self.default_headers.iter().cloned());
        if let Some(extra) = headers {
            for (k, v) in extra {
                if let Some(slot) = req
                    .headers_mut()
                    .iter_mut()
                    .find(|(ek, _)| ek.eq_ignore_ascii_case(&k))
                {
                    slot.1 = v;
                } else {
                    req.headers_mut().push((k, v));
                }
            }
        }
        if let Some(b) = body {
            *req.body_mut() = bytes::Bytes::from_owner(b);
        }
        if let Some(ms) = timeout_ms {
            req = req.with_timeout(Duration::from_millis(ms));
        }

        // DEV: `block_on` returns `Result<F::Output, io::Error>` where the inner is
        // `Result<HttpResponse, HttpClientError>`. The io::Error only appears if the
        // runtime fails to rebuild a fallback (fork chaos); map it to HttpIoError so
        // callers never see a raw error type leak through.
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
    /// Construct a client bound to `runtime` (a `SharedRuntime`).
    ///
    /// `base_url` is `scheme://host[:port][/prefix]` (`http`/`https`) or
    /// `unix:///path/to.sock`. For UDS the request host is fixed to `localhost`.
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        base_url,
        *,
        runtime,
        timeout_ms=2000,
        headers=None,
        max_retries=0,
        retry_initial_delay_ms=100,
        retry_jitter=true,
        treat_http_errors_as_errors=true,
    ))]
    fn new(
        py: Python<'_>,
        base_url: String,
        runtime: PyRef<'_, SharedRuntimePy>,
        timeout_ms: u64,
        headers: Option<Vec<(String, String)>>,
        max_retries: u32,
        retry_initial_delay_ms: u64,
        retry_jitter: bool,
        treat_http_errors_as_errors: bool,
    ) -> PyResult<Self> {
        let rt = runtime.as_arc().clone();

        // Parse the base URL to validate it and extract the canonical base origin.
        // unix:// is not a registered URL scheme so we handle it first; http/https
        // go through url::Url for proper host/port validation.
        let (base, unix_path) = if let Some(path) = base_url.strip_prefix("unix://") {
            if path.is_empty() {
                return Err(http_error_to_pyerr(
                    py,
                    HttpClientError::InvalidConfig(
                        "unix:// base_url requires a socket path".to_string(),
                    ),
                ));
            }
            ("http://localhost".to_string(), Some(path.to_string()))
        } else {
            let parsed = Url::parse(&base_url).map_err(|e| {
                http_error_to_pyerr(
                    py,
                    HttpClientError::InvalidConfig(format!("invalid base_url '{base_url}': {e}")),
                )
            })?;
            match parsed.scheme() {
                "http" | "https" => {}
                other => {
                    return Err(http_error_to_pyerr(
                        py,
                        HttpClientError::InvalidConfig(format!(
                            "unsupported scheme '{other}' in base_url '{base_url}' (use http, https, or unix)"
                        )),
                    ))
                }
            }
            if parsed.host().is_none() {
                return Err(http_error_to_pyerr(
                    py,
                    HttpClientError::InvalidConfig(format!("base_url '{base_url}' has no host")),
                ));
            }
            // Reconstruct as scheme://authority[/path] with no trailing slash.
            // DEV: use `parsed.authority()` rather than `host_str()` so that
            // IPv6 literals keep their brackets (e.g. `[::1]:8126`).
            let mut base = format!("{}://{}", parsed.scheme(), parsed.authority());
            let path = parsed.path().trim_end_matches('/');
            if !path.is_empty() {
                base.push_str(path);
            }
            (base, None)
        };

        // libdd requires base_url to be set even though it routes on the request
        // URL we build; set it to the origin for clarity.
        let mut builder = HttpClient::builder()
            .base_url(base.clone())
            .timeout(Duration::from_millis(timeout_ms))
            .treat_http_errors_as_errors(treat_http_errors_as_errors);
        if max_retries > 0 {
            builder = builder.retry(
                RetryConfig::new()
                    .max_retries(max_retries)
                    .initial_delay(Duration::from_millis(retry_initial_delay_ms))
                    .with_jitter(retry_jitter),
            );
        }
        if let Some(path) = unix_path {
            #[cfg(unix)]
            {
                builder = builder.unix_socket(std::path::PathBuf::from(path));
            }
            #[cfg(not(unix))]
            {
                let _ = path;
                return Err(PyValueError::new_err(
                    "Unix sockets are not supported on this platform",
                ));
            }
        }
        let inner = builder.build().map_err(|e| http_error_to_pyerr(py, e))?;

        Ok(Self {
            inner: Some((inner, rt)),
            base,
            default_headers: headers.unwrap_or_default(),
        })
    }

    /// GET `path` (joined onto the base URL).
    #[pyo3(signature = (path, *, headers=None, timeout_ms=None))]
    fn get(
        &self,
        py: Python<'_>,
        path: String,
        headers: Option<Vec<(String, String)>>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        self.request(py, HttpMethod::Get, &path, headers, None, timeout_ms)
    }

    /// HEAD `path`.
    #[pyo3(signature = (path, *, headers=None, timeout_ms=None))]
    fn head(
        &self,
        py: Python<'_>,
        path: String,
        headers: Option<Vec<(String, String)>>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        self.request(py, HttpMethod::Head, &path, headers, None, timeout_ms)
    }

    /// DELETE `path`.
    #[pyo3(signature = (path, *, headers=None, timeout_ms=None))]
    fn delete(
        &self,
        py: Python<'_>,
        path: String,
        headers: Option<Vec<(String, String)>>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        self.request(py, HttpMethod::Delete, &path, headers, None, timeout_ms)
    }

    /// POST `path` with an optional body.
    #[pyo3(signature = (path, *, headers=None, body=None, timeout_ms=None))]
    fn post(
        &self,
        py: Python<'_>,
        path: String,
        headers: Option<Vec<(String, String)>>,
        body: Option<PyBackedBytes>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        self.request(py, HttpMethod::Post, &path, headers, body, timeout_ms)
    }

    /// PUT `path` with an optional body.
    #[pyo3(signature = (path, *, headers=None, body=None, timeout_ms=None))]
    fn put(
        &self,
        py: Python<'_>,
        path: String,
        headers: Option<Vec<(String, String)>>,
        body: Option<PyBackedBytes>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        self.request(py, HttpMethod::Put, &path, headers, body, timeout_ms)
    }

    /// PATCH `path` with an optional body.
    #[pyo3(signature = (path, *, headers=None, body=None, timeout_ms=None))]
    fn patch(
        &self,
        py: Python<'_>,
        path: String,
        headers: Option<Vec<(String, String)>>,
        body: Option<PyBackedBytes>,
        timeout_ms: Option<u64>,
    ) -> PyResult<HttpResponsePy> {
        self.request(py, HttpMethod::Patch, &path, headers, body, timeout_ms)
    }

    /// Explicit shutdown — releases the connection pool and tokio handle
    /// immediately rather than waiting for garbage collection.
    ///
    /// Calling this is optional: `__exit__` (context manager) and `__del__`
    /// (GC / refcount-zero) both run the same path. Use `shutdown()` when you
    /// want a deterministic teardown boundary (e.g. before fork, or at the end
    /// of a request-handling scope where latency matters).
    fn shutdown(&mut self) {
        drop(self.inner.take());
    }

    fn __enter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __exit__(
        &mut self,
        _exc_type: &Bound<'_, PyAny>,
        _exc_val: &Bound<'_, PyAny>,
        _exc_tb: &Bound<'_, PyAny>,
    ) -> bool {
        self.shutdown();
        false // do not suppress exceptions
    }

    fn __repr__(&self) -> String {
        let state = if self.inner.is_some() {
            "open"
        } else {
            "closed"
        };
        format!("HTTPClient(base={:?}, <{}>)", self.base, state)
    }
}
