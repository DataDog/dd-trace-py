//! Native Remote Config client.
//!
//! Wraps libdatadog's single-target [`SingleChangesFetcher`] and exposes it to
//! Python.
//!
//! Single-process: `poll()` fetches and returns the per-poll changes directly;
//! the storage keeps the config files in-process.
//!
//! Multi-process: the origin calls `enable_shared_memory()` *before* forking, which
//! moves the storage into shared memory (see [`crate::rc_shm`]): forked children
//! inherit the ShmHandles, to obtain a [`RemoteConfigReader`] via `make_reader()`,
//! and diffing successive manifests into add/update/remove changes.

use std::collections::HashSet;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use libdd_common::tag::Tag;
use libdd_common::Endpoint;
use libdd_remote_config::fetch::{
    ConfigApplyState, ConfigInvariants, ConfigOptions, SingleChangesFetcher,
};
use libdd_remote_config::file_change_tracker::{Change, FilePath};
use libdd_remote_config::{
    RemoteConfigCapabilities as RemoteConfigCapabilitiesNative, RemoteConfigPath,
    RemoteConfigProduct as RemoteConfigProductNative, Target,
};
use libdd_shared_runtime::{BlockingRuntime, ForkSafeRuntime};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use native_proc_macro::ConvertToPyO3Enum;

use crate::rc_shm::{ShmReader, ShmStorage};
use crate::shared_runtime::SharedRuntimePy;

#[pyclass(eq, hash, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Hash, ConvertToPyO3Enum)]
pub struct RemoteConfigProduct(pub RemoteConfigProductNative);

#[pyclass(eq, hash, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Hash, ConvertToPyO3Enum)]
pub struct RemoteConfigCapabilities(pub RemoteConfigCapabilitiesNative);

/// A single remote-config change as handed to Python.
///
/// `content` is the raw unparsed config bytes for adds/updates, or `None` to
/// signal a deletion.
#[pyclass(frozen, name = "RemoteConfigChange", skip_from_py_object)]
#[derive(Clone)]
pub struct ChangeRecord {
    #[pyo3(get)]
    pub path: String,
    #[pyo3(get)]
    pub product: RemoteConfigProduct,
    #[pyo3(get)]
    pub config_id: String,
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub version: u64,
    pub content: Option<Vec<u8>>,
}

#[pymethods]
impl ChangeRecord {
    /// Raw config bytes (`bytes`) for an add/update, or `None` for a removal.
    #[getter]
    fn content<'py>(&self, py: Python<'py>) -> Option<Bound<'py, PyBytes>> {
        self.content.as_ref().map(|b| PyBytes::new(py, b))
    }
}

impl ChangeRecord {
    /// Build a [`ChangeRecord`] from a parsed config path.
    /// `content` is `Some` for an add/update, `None` for a removal.
    pub fn new(path: &RemoteConfigPath, version: u64, content: Option<Vec<u8>>) -> Self {
        Self {
            path: path.to_string(),
            product: RemoteConfigProduct(path.product),
            config_id: path.config_id.clone(),
            name: path.name.clone(),
            version,
            content,
        }
    }
}

fn tags_from(pairs: Option<Vec<(String, String)>>) -> Vec<Tag> {
    pairs
        .unwrap_or_default()
        .into_iter()
        .filter_map(|(k, v)| Tag::new(k, v).ok())
        .collect()
}

struct Inner {
    fetcher: SingleChangesFetcher<ShmStorage>,
    capabilities: HashSet<RemoteConfigCapabilitiesNative>,
}

/// Native single-target remote config client. Owned by the polling (origin)
/// process; children use [`RemoteConfigReader`] instead.
#[pyclass(frozen)]
pub struct RemoteConfigClient {
    inner: Mutex<Inner>,
    runtime: Arc<ForkSafeRuntime>,
}

impl RemoteConfigClient {
    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }
}

#[pymethods]
impl RemoteConfigClient {
    /// Starts with empty capabilities and products, added from python side.
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        runtime,
        *,
        agent_url,
        tracer_version,
        client_id,
        runtime_id,
        service,
        env,
        app_version,
        language=None,
        tags=None,
        process_tags=None,
        timeout_ms=5000,
        test_session_token=None,
    ))]
    fn new(
        runtime: PyRef<'_, SharedRuntimePy>,
        agent_url: String,
        tracer_version: String,
        client_id: String,
        runtime_id: String,
        service: String,
        env: String,
        app_version: String,
        language: Option<String>,
        tags: Option<Vec<(String, String)>>,
        process_tags: Option<Vec<(String, String)>>,
        timeout_ms: u64,
        test_session_token: Option<String>,
    ) -> PyResult<Self> {
        let rt = runtime.as_arc().clone();

        let mut endpoint = Endpoint::from_slice(&agent_url);
        endpoint.timeout_ms = timeout_ms;
        if let Some(token) = test_session_token {
            endpoint.test_token = Some(token.into());
        }

        let options = ConfigOptions {
            invariants: ConfigInvariants {
                language: language.unwrap_or_else(|| "python".to_string()),
                tracer_version,
                endpoint,
            },
            products: Vec::new(),
            capabilities: Vec::new(),
        };

        let target = Target {
            service,
            env,
            app_version,
            tags: tags_from(tags),
            process_tags: tags_from(process_tags),
        };

        // The fetcher owns the storage; it's reached later via
        // `fetcher.fetcher.file_storage()`.
        let fetcher = SingleChangesFetcher::new(ShmStorage::new(), target, runtime_id, options)
            .with_client_id(client_id);

        Ok(RemoteConfigClient {
            inner: Mutex::new(Inner {
                fetcher,
                capabilities: HashSet::new(),
            }),
            runtime: rt,
        })
    }

    /// Add capabilities the client advertises to the agent.
    fn add_capabilities(&self, capabilities: Vec<RemoteConfigCapabilities>) {
        self.lock().capabilities.extend(
            capabilities
                .into_iter()
                .map(RemoteConfigCapabilitiesNative::from),
        );
    }

    /// Replace the capabilities within `mask` by `capabilities` (their intersection
    /// with `mask`), leaving capabilities outside `mask` untouched. Unlike
    /// [`add_capabilities`], this can *clear* bits — used to follow one-click
    /// activation/deactivation, where the set of advertised capabilities shrinks
    /// again once a product is turned off.
    fn update_capabilities(
        &self,
        mask: Vec<RemoteConfigCapabilities>,
        capabilities: Vec<RemoteConfigCapabilities>,
    ) {
        let mask: HashSet<RemoteConfigCapabilitiesNative> =
            mask.into_iter().map(Into::into).collect();
        let mut inner = self.lock();
        inner.capabilities.retain(|c| !mask.contains(c));
        inner.capabilities.extend(
            capabilities
                .into_iter()
                .map(RemoteConfigCapabilitiesNative::from)
                .filter(|c| mask.contains(c)),
        );
    }

    /// Perform a single fetch against the agent and return the per-poll changes.
    ///
    /// `products` (enabled products) and `extra_services` (runtime-discovered
    /// services) are the per-poll inputs that change at runtime; they — and the
    /// capabilities accumulated via `add_capabilities` — are pushed to the fetcher
    /// before fetching. When broadcast is enabled, the fetch updates the SHM
    /// storage in place (store/update/remove) and wakes waiting readers.
    #[pyo3(signature = (products, extra_services))]
    fn poll(
        &self,
        py: Python<'_>,
        products: Vec<RemoteConfigProduct>,
        extra_services: Vec<String>,
    ) -> PyResult<Vec<ChangeRecord>> {
        // Unwrap the PyO3 mirror to the libdatadog enum before releasing the GIL.
        let products: Vec<RemoteConfigProductNative> =
            products.into_iter().map(Into::into).collect();
        let result: Result<Vec<ChangeRecord>, String> = py.detach(move || {
            let mut inner = self.lock();

            let rc_capabilities: Vec<RemoteConfigCapabilitiesNative> =
                inner.capabilities.iter().copied().collect();
            inner
                .fetcher
                .set_product_capabilities(products, rc_capabilities);
            inner.fetcher.set_extra_services(extra_services);

            let changes = match self
                .runtime
                .block_on(inner.fetcher.fetch_changes::<Vec<u8>>())
            {
                Ok(Ok(changes)) => changes,
                Ok(Err(e)) => return Err(format!("remote config fetch error: {e}")),
                Err(e) => return Err(format!("remote config runtime error: {e}")),
            };

            // Build the delta records for the origin's own dispatch (ChangeTracker
            // already orders removals first). `store`/`update` updated the SHM as a
            // side effect of the fetch; removals aren't a `FileStorage` hook, so we
            // drive them here from the delta. Content is read back from the storage.
            let storage = inner.fetcher.fetcher.file_storage();
            let mut records = Vec::with_capacity(changes.len());
            for change in &changes {
                match change {
                    Change::Remove(file) => {
                        let path = file.path();
                        storage.remove(&path.to_string());
                        records.push(ChangeRecord::new(path, file.version(), None));
                    }
                    Change::Add(file) | Change::Update(file, _) => {
                        let path = file.path();
                        let content = storage.contents_for(&path.to_string());
                        records.push(ChangeRecord::new(path, file.version(), Some(content)));
                    }
                }
            }
            Ok(records)
        });

        result.map_err(PyRuntimeError::new_err)
    }

    /// Acknowledge or report an error for an applied config. `error=None`
    /// acknowledges; `error=Some(msg)` reports a (e.g. parse) failure to the agent.
    #[pyo3(signature = (path, error=None))]
    fn set_config_state(&self, path: &str, error: Option<String>) -> PyResult<()> {
        let rcp: RemoteConfigPath = RemoteConfigPath::try_parse(path)
            .map_err(|e| PyValueError::new_err(format!("invalid config path {path}: {e}")))?
            .into();
        let state = match error {
            Some(msg) => ConfigApplyState::Error(msg),
            None => ConfigApplyState::Acknowledged,
        };
        self.lock().fetcher.fetcher.set_config_state(&rcp, state);
        Ok(())
    }

    /// The remote config client id (a UUID). Stable for the life of the process.
    fn get_client_id(&self) -> String {
        self.lock().fetcher.get_client_id().clone()
    }

    /// Enable cross-process broadcast: move the storage into shared memory. Must
    /// be called on the origin *before* forking so children inherit the segments.
    /// Idempotent; re-seeds the manifest so a child forked immediately afterwards
    /// sees the already-applied config.
    fn enable_shared_memory(&self) -> PyResult<()> {
        self.lock()
            .fetcher
            .fetcher
            .file_storage()
            .enable_shared_memory()
            .map_err(|e| {
                PyRuntimeError::new_err(format!("failed to enable remote config broadcast: {e}"))
            })
    }

    /// Create a reader over the broadcast segments. Called in a forked child (on
    /// the inherited client object) to consume configs published by the origin.
    /// The RemoteConfigClient instance must not be used anymore after this call.
    fn make_reader(&self) -> PyResult<RemoteConfigReader> {
        let (manifest_handle, contents_handle) = self
            .lock()
            .fetcher
            .fetcher
            .file_storage()
            .take_handles()
            .ok_or_else(|| PyRuntimeError::new_err("remote config broadcast not enabled"))?;
        let reader = ShmReader::new(manifest_handle, contents_handle)
            .map_err(|e| PyRuntimeError::new_err(format!("failed to map RC shared memory: {e}")))?;
        Ok(RemoteConfigReader {
            reader: Mutex::new(reader),
        })
    }
}

/// Consumer side of the cross-process broadcast (forked child processes). Reads
/// the published manifest + contents and diffs successive states into changes.
#[pyclass(frozen)]
pub struct RemoteConfigReader {
    reader: Mutex<ShmReader>,
}

#[pymethods]
impl RemoteConfigReader {
    /// Block until the origin notifies, or `timeout_ms` elapses.
    /// Returns `True` if woken by a notification, `False` on timeout. On Linux
    /// this is a futex wait on the manifest's generation word; elsewhere it polls.
    fn wait_for_change(&self, py: Python<'_>, timeout_ms: u64) -> bool {
        py.detach(move || {
            let mut reader = self.reader.lock().unwrap_or_else(|e| e.into_inner());
            reader.wait_for_change(Duration::from_millis(timeout_ms))
        })
    }

    /// Read the latest published state and return the changes since the last read
    /// (removals first, then adds/updates). An empty list means nothing changed (a
    /// bare poll tick → the caller runs periodic work only).
    ///
    /// `enabled_products` is the set of product names the caller currently
    /// subscribes to; configs for other products are withheld from the active set
    /// so they are (re-)emitted once their product is enabled.
    #[pyo3(signature = (enabled_products))]
    fn read(&self, py: Python<'_>, enabled_products: Vec<String>) -> PyResult<Vec<ChangeRecord>> {
        let enabled: HashSet<String> = enabled_products.into_iter().collect();
        let changes = py.detach(move || {
            let mut reader = self.reader.lock().unwrap_or_else(|e| e.into_inner());
            reader.read(enabled)
        });
        Ok(changes)
    }

    /// Forget what this reader has already delivered, so the next `read()`
    /// returns the full active snapshot.
    fn reset(&self) {
        self.reader
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .reset();
    }
}

pub fn register_remote_config(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RemoteConfigClient>()?;
    m.add_class::<RemoteConfigReader>()?;
    m.add_class::<ChangeRecord>()?;
    // These register their class *and* populate one attribute per enum variant.
    RemoteConfigCapabilities::register(m)?;
    RemoteConfigProduct::register(m)?;
    Ok(())
}
