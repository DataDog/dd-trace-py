//! Sender for the symbol database (SymDB).
//!
//! SymDB rides the same intake host as the debugger tracks but nothing else is
//! shared.

use std::sync::Arc;
use std::time::Duration;

use datadog_live_debugger::sender::{self, Config as SenderConfig};
use libdd_common::Endpoint;
use libdd_shared_runtime::ForkSafeRuntime;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;

use crate::debugger::{build_endpoint, do_send};
use crate::shared_runtime::SharedRuntimePy;

// The endpoint plumbing lives in `debugger` rather than here because SymDB uploads
// reach Datadog through the debugger intake: same host, same agentless route, and
// libdatadog derives both from one `Config`.

#[pyclass(name = "SymDBSender", frozen)]
pub struct SymDBSenderPy {
    runtime: Arc<ForkSafeRuntime>,
    config: SenderConfig,
    url: Endpoint,
    /// Tags as `key:value,key:value`, sent verbatim in the `X-Datadog-Additional-Tags` header.
    tags: String,
    timeout: Duration,
    agentless: bool,
}

#[pymethods]
impl SymDBSenderPy {
    #[new]
    #[pyo3(signature = (
        runtime,
        *,
        url = None,
        site = None,
        api_key = None,
        tags = String::new(),
        timeout_ms = 5_000,
        test_session_token = None,
    ))]
    fn new(
        runtime: PyRef<'_, SharedRuntimePy>,
        url: Option<String>,
        site: Option<String>,
        api_key: Option<String>,
        tags: String,
        timeout_ms: u64,
        test_session_token: Option<String>,
    ) -> PyResult<Self> {
        let agentless = api_key.is_some();
        let endpoint = build_endpoint("symdb", url, site, api_key, timeout_ms, test_session_token)?;

        let mut config = SenderConfig::default();
        config
            .set_symdb_endpoint(endpoint.clone())
            .map_err(|e| PyValueError::new_err(format!("invalid symdb endpoint: {e}")))?;

        Ok(Self {
            runtime: runtime.as_arc().clone(),
            config,
            url: endpoint,
            tags,
            timeout: Duration::from_millis(timeout_ms),
            agentless,
        })
    }

    #[getter]
    fn agentless(&self) -> bool {
        self.agentless
    }

    /// POST a SymDB payload verbatim, blocking until the response arrives.
    /// `content_type` is the caller's multipart content type.
    ///
    /// Returns `None` when the payload was accepted, or `(status, body)` when the
    /// server rejected it with a >= 400 status. Raises `DebuggerSenderError` if
    /// the request never completed.
    fn send(
        &self,
        py: Python<'_>,
        payload: PyBackedBytes,
        content_type: &str,
    ) -> PyResult<Option<(u16, String)>> {
        do_send(py, &self.runtime, self.timeout, async move {
            sender::send_symdb(&payload, content_type, &self.config, &self.tags).await
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "SymDBSender(url={:?}, agentless={})",
            self.url.url.to_string(),
            self.agentless,
        )
    }
}

pub fn register_symdb(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SymDBSenderPy>()?;
    Ok(())
}
