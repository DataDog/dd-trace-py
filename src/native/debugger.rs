//! Sender for Dynamic Instrumentation logs, snapshots and probe diagnostics.
//!
//! A thin PyO3 wrapper around `datadog_live_debugger::sender`.
//!
//! Each send runs `runtime.block_on(...)` inside `py.detach`, releasing the GIL
//! for the duration of the I/O. The uploader already runs on its own periodic thread,
//! so blocking there is what the Python code expects.

use std::borrow::Cow;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use datadog_live_debugger::sender::{
    self, debugger_intake_endpoint, Config as SenderConfig, DebuggerType, PayloadRejected,
};
use libdd_common::{parse_uri, Endpoint};
use libdd_shared_runtime::{BlockingRuntime, ForkSafeRuntime};
use native_proc_macro::ConvertToPyO3Enum;
use percent_encoding::{percent_encode, AsciiSet, NON_ALPHANUMERIC};
use pyo3::create_exception;
use pyo3::exceptions::{PyException, PyValueError};
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;

use crate::shared_runtime::SharedRuntimePy;

create_exception!(
    debugger,
    DebuggerSenderError,
    PyException,
    "A payload could not be delivered to the debugger intake (transport failure or timeout)."
);

pub(crate) fn build_endpoint(
    what: &'static str,
    url: Option<String>,
    site: Option<String>,
    api_key: Option<String>,
    timeout_ms: u64,
    test_session_token: Option<String>,
) -> PyResult<Endpoint> {
    let mut endpoint = match (url, site, api_key) {
        (Some(url), _, api_key) => {
            let mut endpoint = Endpoint::from_url(parse_uri(&url).map_err(|e| {
                PyValueError::new_err(format!("invalid {what} endpoint url '{url}': {e}"))
            })?);
            endpoint.api_key = api_key.map(Cow::Owned);
            endpoint
        }
        (None, Some(site), Some(api_key)) => debugger_intake_endpoint(&site, api_key)
            .map_err(|e| PyValueError::new_err(format!("invalid {what} intake site: {e}")))?,
        (None, _, _) => {
            return Err(PyValueError::new_err(format!(
                "{what} sender requires either `url` or both `site` and `api_key`"
            )))
        }
    };
    endpoint.timeout_ms = timeout_ms;
    endpoint.test_token = test_session_token.map(Cow::Owned);

    Ok(endpoint)
}

/// A response from the debugger payload receiver.
///
/// libdatadog doesn't report the status code on success, but fine, it's not important to us.
#[pyclass(name = "DebuggerResponse", frozen)]
pub struct DebuggerResponsePy {
    accepted: bool,
    status: Option<u16>,
    body: String,
}

impl DebuggerResponsePy {
    fn new_accepted() -> Self {
        Self {
            accepted: true,
            status: None,
            body: String::new(),
        }
    }

    fn new_rejected(status: u16, body: String) -> Self {
        Self {
            accepted: false,
            status: Some(status),
            body,
        }
    }
}

#[pymethods]
impl DebuggerResponsePy {
    /// Whether the intake took the payload.
    #[getter]
    fn accepted(&self) -> bool {
        self.accepted
    }

    /// The response status, or `None` when the payload was accepted.
    #[getter]
    fn status(&self) -> Option<u16> {
        self.status
    }

    /// The response body. Empty unless the payload was rejected.
    #[getter]
    fn body(&self) -> &str {
        &self.body
    }

    fn __repr__(&self) -> String {
        match self.status {
            Some(status) => format!(
                "DebuggerResponse(accepted=False, status={}, body_len={})",
                status,
                self.body.len()
            ),
            None => "DebuggerResponse(accepted=True)".to_string(),
        }
    }
}

/// Run `future` on the shared runtime with the GIL released, bounded by `timeout`,
/// and turn the outcome into a [`DebuggerResponsePy`].
///
/// A rejection is a response, not an error: whether a status is worth retrying,
/// downgrading for, or dropping is the caller's decision. Only a request that
/// never produced a response at all — transport failure, timeout — raises.
pub(crate) fn do_send<F>(
    py: Python<'_>,
    runtime: &Arc<ForkSafeRuntime>,
    timeout: Duration,
    future: F,
) -> PyResult<DebuggerResponsePy>
where
    F: std::future::Future<Output = anyhow::Result<()>> + Send,
{
    let runtime = runtime.clone();
    let result = py.detach(move || {
        runtime.block_on(async move {
            match tokio::time::timeout(timeout, future).await {
                Ok(result) => result,
                Err(_) => Err(anyhow::anyhow!("timed out after {}ms", timeout.as_millis())),
            }
        })
    });

    match result {
        Ok(Ok(())) => Ok(DebuggerResponsePy::new_accepted()),
        Ok(Err(e)) => match e.downcast_ref::<PayloadRejected>() {
            Some(rejected) => Ok(DebuggerResponsePy::new_rejected(
                rejected.status,
                rejected.body.clone(),
            )),
            None => Err(DebuggerSenderError::new_err(format!("{e:#}"))),
        },
        // The io::Error only shows up if the shared runtime failed to rebuild a
        // fallback (fork chaos); surface it as a send failure rather than letting a
        // raw error type leak into Python.
        Err(io_err) => Err(DebuggerSenderError::new_err(format!(
            "shared runtime block_on failed: {io_err}"
        ))),
    }
}

/// The characters to escape in the `ddtags` query string: everything bar the
/// unreserved set and `/`, matching Python's `urllib.parse.quote` defaults.
///
/// This has to be conservative rather than minimal, because tag values come from
/// `DD_TAGS` and can hold anything — a space in a tag value is legal for the user
/// and illegal in a URI, so under-escaping fails the whole request.
const DDTAGS_PERCENT_ENCODED_SET: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'_')
    .remove(b'.')
    .remove(b'~')
    .remove(b'/');

/// Which debugger track a payload belongs to.
#[pyclass(eq, hash, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Hash, ConvertToPyO3Enum)]
pub struct DebuggerTrackType(pub DebuggerType);

/// The endpoint configuration, plus whether the logs/snapshots tracks have been
/// downgraded onto the diagnostics endpoint. Guarded by one mutex so a downgrade
/// and the flag can never disagree.
struct State {
    config: SenderConfig,
    downgraded: bool,
}

#[pyclass(name = "DebuggerSender", frozen)]
pub struct DebuggerSenderPy {
    runtime: Arc<ForkSafeRuntime>,
    state: Mutex<State>,
    /// The endpoint the tracks were originally derived from, kept so
    /// `reset_endpoints` can undo a downgrade.
    base_endpoint: Endpoint,
    /// Tags percent-encoded for the `ddtags` query string.
    encoded_tags: String,
    timeout: Duration,
    agentless: bool,
}

impl DebuggerSenderPy {
    fn with_state<T>(&self, f: impl FnOnce(&mut State) -> T) -> T {
        let mut guard = self
            .state
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        f(&mut guard)
    }
}

#[pymethods]
impl DebuggerSenderPy {
    /// Build a sender bound to `runtime` (a `SharedRuntime`).
    ///
    /// `tags` is the unencoded `key:value,key:value` string; it is percent-encoded
    /// here for the `ddtags` query string.
    #[new]
    #[pyo3(signature = (
        runtime,
        *,
        url = None,
        site = None,
        api_key = None,
        tags = String::new(),
        timeout_ms = 30_000,
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
        let endpoint = build_endpoint(
            "debugger",
            url,
            site,
            api_key,
            timeout_ms,
            test_session_token,
        )?;

        let mut config = SenderConfig::default();
        config
            .set_endpoint(endpoint.clone())
            .map_err(|e| PyValueError::new_err(format!("invalid debugger endpoint: {e}")))?;

        Ok(Self {
            runtime: runtime.as_arc()?,
            state: Mutex::new(State {
                config,
                downgraded: false,
            }),
            base_endpoint: endpoint,
            encoded_tags: percent_encode(tags.as_bytes(), DDTAGS_PERCENT_ENCODED_SET).to_string(),
            timeout: Duration::from_millis(timeout_ms),
            agentless,
        })
    }

    /// Whether payloads are submitted directly to the intake (`True`) or through
    /// the local trace agent (`False`).
    #[getter]
    fn agentless(&self) -> bool {
        self.agentless
    }

    /// Point the logs and snapshots tracks at the diagnostics endpoint, for
    /// agents that do not proxy `/debugger/v2/input`.
    ///
    /// A no-op in agentless mode, where all three tracks already share one
    /// receiver path; returns whether anything changed.
    fn downgrade_to_diagnostics(&self) -> bool {
        if self.agentless {
            return false;
        }
        self.with_state(|state| {
            if state.downgraded {
                return false;
            }
            state.config.downgrade_to_diagnostics_endpoint();
            state.downgraded = true;
            true
        })
    }

    /// Undo a downgrade, restoring the endpoints derived at construction.
    fn reset_endpoints(&self) -> PyResult<()> {
        self.with_state(|state| {
            if !state.downgraded {
                return Ok(());
            }
            state
                .config
                .set_endpoint(self.base_endpoint.clone())
                .map_err(|e| PyValueError::new_err(format!("invalid debugger endpoint: {e}")))?;
            state.downgraded = false;
            Ok(())
        })
    }

    /// POST a JSON array of debugger payloads (`[{...},{...}]`) to `debugger_type`'s
    /// endpoint, blocking until the response arrives.
    ///
    /// Returns the receiver's `DebuggerResponse`. Raises `DebuggerSenderError` if
    /// the request never completed.
    fn send(
        &self,
        py: Python<'_>,
        payload: PyBackedBytes,
        debugger_type: DebuggerTrackType,
    ) -> PyResult<DebuggerResponsePy> {
        // Snapshot the config so the send does not hold the lock across I/O.
        let config = self.with_state(|state| state.config.clone());
        do_send(py, &self.runtime, self.timeout, async move {
            sender::send(&payload, &config, debugger_type.0, &self.encoded_tags).await
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "DebuggerSender(url={:?}, agentless={}, downgraded={})",
            self.base_endpoint.url.to_string(),
            self.agentless,
            self.with_state(|state| state.downgraded),
        )
    }
}

pub fn register_debugger(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DebuggerSenderPy>()?;
    m.add_class::<DebuggerResponsePy>()?;
    DebuggerTrackType::register(m)?;
    m.add(
        "DebuggerSenderError",
        m.py().get_type::<DebuggerSenderError>(),
    )?;
    Ok(())
}
