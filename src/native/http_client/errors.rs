//! Python exception hierarchy for the native HTTP client.
//!
//! `HttpClientError` is a real `#[pyclass]` (extending `PyException`) so we can
//! have subclasses with extra attributes (notably `RequestFailedError.status`
//! and `.body`). The simpler variants (`ConnectionFailedError`,
//! `TimedOutError`, `InvalidConfigError`, `HttpIoError`) are derived from
//! `create_exception!`, which accepts any base whose type implements
//! `PyTypeInfo` — `#[pyclass]` types qualify.
//!
//! DEV: Conversion from a libdd `HttpClientError` to a Python exception
//! happens via `http_error_to_pyerr`. That function must only be called with
//! the GIL held — it is invoked from `client.rs` after `py.detach` returns.

use libdd_http_client::HttpClientError;
use pyo3::{create_exception, exceptions::PyException, prelude::*, PyErr};

/// Base class. Real `pyclass` so subclasses can `extends = HttpClientError`
/// and so Python code can subclass it.
#[pyclass(name = "HttpClientError", extends = PyException, subclass)]
pub struct HttpClientErrorPy;

#[pymethods]
impl HttpClientErrorPy {
    #[new]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(_args: Bound<'_, PyAny>, _kwargs: Option<Bound<'_, PyAny>>) -> Self {
        // DEV: args/kwargs are accepted and discarded so the base is
        // constructible (and subclassable) from Python; PyException handles
        // the message itself.
        HttpClientErrorPy
    }
}

create_exception!(
    http_client,
    ConnectionFailedError,
    HttpClientErrorPy,
    "TCP/socket connection to the server could not be established."
);
create_exception!(
    http_client,
    TimedOutError,
    HttpClientErrorPy,
    "The request exceeded its configured timeout."
);
create_exception!(
    http_client,
    InvalidConfigError,
    HttpClientErrorPy,
    "The client or request configuration was invalid."
);
// DEV: named `HttpIoError` to avoid a collision with `data_pipeline::IoError`
// already exported on the same `_native` module.
create_exception!(
    http_client,
    HttpIoError,
    HttpClientErrorPy,
    "An I/O error occurred during the request."
);

/// HTTP 4xx/5xx response. Only raised when
/// `treat_http_errors_as_errors=True` (the default).
#[pyclass(name = "RequestFailedError", extends = HttpClientErrorPy)]
pub struct RequestFailedErrorPy {
    #[pyo3(get)]
    status: u16,
    #[pyo3(get)]
    body: String,
}

#[pymethods]
impl RequestFailedErrorPy {
    #[new]
    fn new(status: u16, body: String) -> (Self, HttpClientErrorPy) {
        (Self { status, body }, HttpClientErrorPy)
    }

    fn __str__(&self) -> String {
        format!("request failed with status {}: {}", self.status, self.body)
    }

    fn __repr__(&self) -> String {
        format!(
            "RequestFailedError(status={}, body={:?})",
            self.status, self.body
        )
    }
}

/// Convert a libdd `HttpClientError` into the matching Python exception.
///
/// **The caller must hold the GIL.**
pub fn http_error_to_pyerr(py: Python<'_>, err: HttpClientError) -> PyErr {
    match err {
        HttpClientError::ConnectionFailed(m) => ConnectionFailedError::new_err(m),
        HttpClientError::TimedOut => TimedOutError::new_err("request timed out"),
        HttpClientError::RequestFailed { status, body } => {
            let init = PyClassInitializer::from(HttpClientErrorPy)
                .add_subclass(RequestFailedErrorPy { status, body });
            match Py::new(py, init) {
                Ok(obj) => PyErr::from_value(obj.into_bound(py).into_any()),
                Err(e) => e,
            }
        }
        HttpClientError::InvalidConfig(m) => InvalidConfigError::new_err(m),
        HttpClientError::IoError(m) => HttpIoError::new_err(m),
    }
}

pub fn register_exceptions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<HttpClientErrorPy>()?;
    m.add(
        "ConnectionFailedError",
        m.py().get_type::<ConnectionFailedError>(),
    )?;
    m.add("TimedOutError", m.py().get_type::<TimedOutError>())?;
    m.add_class::<RequestFailedErrorPy>()?;
    m.add(
        "InvalidConfigError",
        m.py().get_type::<InvalidConfigError>(),
    )?;
    m.add("HttpIoError", m.py().get_type::<HttpIoError>())?;
    Ok(())
}
