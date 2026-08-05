//! Native Dynamic Instrumentation / Live Debugger payload sender.
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
    "A debugger payload could not be delivered (transport failure or timeout)."
);

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
    /// Tags as `key:value,key:value`, sent verbatim in the SymDB
    /// `X-Datadog-Additional-Tags` header.
    tags: String,
    /// `tags`, percent-encoded for the `ddtags` query string.
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

    /// Snapshot the endpoint config so the send does not hold the lock across I/O.
    fn config(&self) -> SenderConfig {
        self.with_state(|state| state.config.clone())
    }

    /// Run `future` on the shared runtime with the GIL released, bounded by the
    /// configured timeout, and map the outcome onto
    /// `Ok(None)` (accepted) / `Ok(Some((status, body)))` (rejected) / `Err`.
    fn run_send<F>(&self, py: Python<'_>, future: F) -> PyResult<Option<(u16, String)>>
    where
        F: std::future::Future<Output = anyhow::Result<()>> + Send,
    {
        let runtime = self.runtime.clone();
        let timeout = self.timeout;
        let result = py.detach(move || {
            runtime.block_on(async move {
                match tokio::time::timeout(timeout, future).await {
                    Ok(result) => result,
                    Err(_) => Err(anyhow::anyhow!("timed out after {}ms", timeout.as_millis())),
                }
            })
        });

        match result {
            Ok(Ok(())) => Ok(None),
            Ok(Err(e)) => match e.downcast_ref::<PayloadRejected>() {
                Some(rejected) => Ok(Some((rejected.status, rejected.body.clone()))),
                None => Err(DebuggerSenderError::new_err(format!("{e:#}"))),
            },
            // The io::Error only shows up if the shared runtime failed to rebuild
            // a fallback (fork chaos); surface it as a send failure rather than
            // letting a raw error type leak into Python.
            Err(io_err) => Err(DebuggerSenderError::new_err(format!(
                "shared runtime block_on failed: {io_err}"
            ))),
        }
    }
}

#[pymethods]
impl DebuggerSenderPy {
    /// Build a sender bound to `runtime` (a `SharedRuntime`).
    ///
    /// Either pass `url` (the trace agent URL — `http`, `https` or
    /// `unix:///path.sock`) for agent-proxied uploads, or `site` + `api_key` to
    /// submit directly to `debugger-intake.{site}`. Passing `url` *and*
    /// `api_key` submits directly to `url`, which is how tests point agentless
    /// mode at a local intake.
    ///
    /// `tags` is the unencoded `key:value,key:value` string; it is
    /// percent-encoded here for the `ddtags` query string.
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

        let mut endpoint = match (url, site, api_key) {
            (Some(url), _, api_key) => {
                let mut endpoint = Endpoint::from_url(parse_uri(&url).map_err(|e| {
                    PyValueError::new_err(format!("invalid debugger endpoint url '{url}': {e}"))
                })?);
                endpoint.api_key = api_key.map(Cow::Owned);
                endpoint
            }
            (None, Some(site), Some(api_key)) => debugger_intake_endpoint(&site, api_key)
                .map_err(|e| PyValueError::new_err(format!("invalid debugger intake site: {e}")))?,
            (None, _, _) => {
                return Err(PyValueError::new_err(
                    "DebuggerSender requires either `url` or both `site` and `api_key`",
                ))
            }
        };
        endpoint.timeout_ms = timeout_ms;
        endpoint.test_token = test_session_token.map(Cow::Owned);

        let mut config = SenderConfig::default();
        config
            .set_endpoint(endpoint.clone())
            .map_err(|e| PyValueError::new_err(format!("invalid debugger endpoint: {e}")))?;
        config
            .set_symdb_endpoint(endpoint.clone())
            .map_err(|e| PyValueError::new_err(format!("invalid symdb endpoint: {e}")))?;

        let encoded_tags = percent_encode(tags.as_bytes(), DDTAGS_PERCENT_ENCODED_SET).to_string();

        Ok(Self {
            runtime: runtime.as_arc().clone(),
            state: Mutex::new(State {
                config,
                downgraded: false,
            }),
            base_endpoint: endpoint,
            tags,
            encoded_tags,
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

    /// Whether the logs and snapshots tracks currently point at the diagnostics
    /// endpoint because of a [`downgrade_to_diagnostics`] call.
    #[getter]
    fn downgraded(&self) -> bool {
        self.with_state(|state| state.downgraded)
    }

    /// Point the logs and snapshots tracks at the diagnostics endpoint, for
    /// agents that do not proxy `/debugger/v2/input`.
    ///
    /// A no-op in agentless mode, where all three tracks already share one
    /// intake path; returns whether anything changed.
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
    /// Returns `None` when the payload was accepted, or `(status, body)` when the
    /// server rejected it with a >= 400 status. Raises `DebuggerSenderError` if
    /// the request never completed.
    fn send(
        &self,
        py: Python<'_>,
        payload: PyBackedBytes,
        debugger_type: DebuggerTrackType,
    ) -> PyResult<Option<(u16, String)>> {
        let config = self.config();
        self.run_send(py, async move {
            sender::send(&payload, &config, debugger_type.0, &self.encoded_tags).await
        })
    }

    /// POST a SymDB payload verbatim to the symbol database endpoint, blocking
    /// until the response arrives. `content_type` is the caller's multipart
    /// content type; the tags ride in `X-Datadog-Additional-Tags`.
    ///
    /// Return value and errors match [`send`].
    fn send_symdb(
        &self,
        py: Python<'_>,
        payload: PyBackedBytes,
        content_type: &str,
    ) -> PyResult<Option<(u16, String)>> {
        let config = self.config();
        self.run_send(py, async move {
            sender::send_symdb(&payload, content_type, &config, &self.tags).await
        })
    }

    fn __repr__(&self) -> String {
        let downgraded = self.downgraded();
        format!(
            "DebuggerSender(url={:?}, agentless={}, downgraded={})",
            self.base_endpoint.url.to_string(),
            self.agentless,
            downgraded,
        )
    }
}

pub fn register_debugger(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DebuggerSenderPy>()?;
    DebuggerTrackType::register(m)?;
    m.add(
        "DebuggerSenderError",
        m.py().get_type::<DebuggerSenderError>(),
    )?;
    Ok(())
}
