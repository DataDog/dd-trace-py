//! PyO3 wrapper around `libdd-http-client`.
//!
//! Exposes `HttpClient`, `HttpClientBuilder`, `HttpRequest`, `HttpResponse`,
//! `HttpMethod`, `MultipartPart`, and the `HttpClientError` exception hierarchy
//! to Python under `ddtrace.internal.native._native`.
//!
//! DEV: The native client does not inject default headers (container-ID etc.) —
//! that stays in the Python wrapper layer at each callsite. See
//! `ddtrace.internal.native.apply_container_headers`.

use pyo3::prelude::*;

mod client;
mod errors;
mod request;
mod response;

// DEV: re-exports kept for downstream Rust callers (none yet); silence
// dead-code warnings when only the Python registration is used.
#[allow(unused_imports)]
pub use client::{HttpClientBuilderPy, HttpClientPy};
#[allow(unused_imports)]
pub use errors::HttpClientErrorPy;
#[allow(unused_imports)]
pub use request::{HttpMethodPy, HttpRequestPy, MultipartPartPy};
#[allow(unused_imports)]
pub use response::HttpResponsePy;

pub fn register_http_client(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<HttpClientPy>()?;
    m.add_class::<HttpClientBuilderPy>()?;
    m.add_class::<HttpRequestPy>()?;
    m.add_class::<HttpResponsePy>()?;
    m.add_class::<MultipartPartPy>()?;
    m.add_class::<HttpMethodPy>()?;
    errors::register_exceptions(m)?;
    Ok(())
}
