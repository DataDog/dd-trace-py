use std::sync::{Arc, Mutex};
use std::time::Duration;

use libdd_ai_guard::{AiGuardX509, AiGuardX509Config, ReceivedPayload};
use libdd_shared_runtime::{BlockingRuntime, ForkSafeRuntime};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use tokio::sync::watch;

use crate::shared_runtime::SharedRuntimePy;

#[pyclass(frozen)]
pub struct AiGuardX509Client {
    inner: Mutex<Option<AiGuardX509>>,
    payload_rx: Mutex<watch::Receiver<Option<ReceivedPayload>>>,
    runtime: Arc<ForkSafeRuntime>,
}

#[pymethods]
impl AiGuardX509Client {
    #[new]
    #[pyo3(signature = (runtime, *, site, api_key, app_name="dd-trace-py", app_version="0.0.0", register_url=None, host_tags=None))]
    fn new(
        runtime: PyRef<'_, SharedRuntimePy>,
        site: String,
        api_key: String,
        app_name: &str,
        app_version: &str,
        register_url: Option<String>,
        host_tags: Option<Vec<String>>,
    ) -> PyResult<Self> {
        let rt = runtime.as_arc()?;
        let config = AiGuardX509Config {
            site,
            api_key,
            app_name: app_name.to_string(),
            app_version: app_version.to_string(),
            register_url,
            host_tags: host_tags.unwrap_or_default(),
        };
        let client = AiGuardX509::start_on_runtime(&rt, config)
            .map_err(|e| PyRuntimeError::new_err(format!("failed to start AiGuardX509: {e}")))?;
        let payload_rx = client.subscribe_payload();
        Ok(Self {
            inner: Mutex::new(Some(client)),
            payload_rx: Mutex::new(payload_rx),
            runtime: rt,
        })
    }

    fn connection_id(&self) -> Option<String> {
        let guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard.as_ref().and_then(AiGuardX509::connection_id)
    }

    fn payload(&self) -> Option<String> {
        let guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        guard
            .as_ref()
            .and_then(AiGuardX509::payload)
            .map(|p| p.as_utf8_lossy())
    }

    #[pyo3(signature = (timeout_ms=5000))]
    fn wait_for_connection_id(&self, py: Python<'_>, timeout_ms: u64) -> Option<String> {
        let rx = {
            let guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
            guard.as_ref().map(AiGuardX509::subscribe_connection_id)
        }?;
        let rt = Arc::clone(&self.runtime);
        py.detach(move || {
            let mut rx = rx;
            rt.block_on(async move {
                tokio::time::timeout(Duration::from_millis(timeout_ms), async {
                    loop {
                        if let Some(cid) = rx.borrow_and_update().as_ref() {
                            return Some(cid.to_string());
                        }
                        if rx.changed().await.is_err() {
                            return None;
                        }
                    }
                })
                .await
                .ok()
                .flatten()
            })
            .ok()
            .flatten()
        })
    }

    #[pyo3(signature = (timeout_ms=1000))]
    fn wait_for_payload(&self, py: Python<'_>, timeout_ms: u64) -> Option<(String, String)> {
        let rt = Arc::clone(&self.runtime);
        py.detach(move || {
            let mut rx = self.payload_rx.lock().unwrap_or_else(|e| e.into_inner());
            rt.block_on(async move {
                match tokio::time::timeout(Duration::from_millis(timeout_ms), rx.changed()).await {
                    Ok(Ok(())) => rx
                        .borrow_and_update()
                        .as_ref()
                        .map(|p| (p.reason.clone(), p.as_utf8_lossy())),
                    _ => None,
                }
            })
            .ok()
            .flatten()
        })
    }

    fn shutdown(&self) {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(client) = guard.take() {
            client.shutdown();
        }
    }
}

pub fn register_ai_guard_x509(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<AiGuardX509Client>()?;
    Ok(())
}
