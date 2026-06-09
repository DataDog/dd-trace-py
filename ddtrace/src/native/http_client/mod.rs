//! PyO3 wrapper around `libdd-http-client`.
//!
//! Exposes a single ergonomic `HTTPClient` class plus `HttpResponse` and the
//! `HttpClientError` exception hierarchy to Python under
//! `ddtrace.internal.native._native`. The low-level libdd builder/request/method
//! types are consumed in Rust and are intentionally NOT exposed to Python.
//!
//! DEV: The native client is a pure transport with base-URL + default-header
//! conveniences. It does not inject default headers (container-ID etc.) — any
//! such header policy belongs in a layer above this client.

use pyo3::prelude::*;

mod client;
mod errors;
mod response;

use client::HttpClientPy;
use response::HttpResponsePy;

pub fn register_http_client(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<HttpClientPy>()?;
    m.add_class::<HttpResponsePy>()?;
    errors::register_exceptions(m)?;
    Ok(())
}
