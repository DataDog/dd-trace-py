//! Native instrumentation-telemetry worker.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use libdd_telemetry::config::{Config, TelemetryEndpoint};
use libdd_telemetry::data::metrics::{
    MetricNamespace as MetricNamespaceNative, MetricType as MetricTypeNative,
};
use libdd_telemetry::data::{
    self, ConfigurationOrigin as ConfigurationOriginNative, DependencyMetadata, Host,
    LogLevel as LogLevelNative,
};
use libdd_telemetry::metrics::ContextKey;
use libdd_telemetry::worker::{
    LifecycleAction, TelemetryActions, TelemetryWorkerBuilder, TelemetryWorkerFlavor,
    TelemetryWorkerHandle,
};
use libdd_telemetry::{parse_tags, Tag};

use libdd_capabilities_impl::NativeCapabilities;
use libdd_shared_runtime::{BlockingRuntime, ForkSafeRuntime, SharedRuntime, WorkerHandle};
use native_proc_macro::ConvertToPyO3Enum;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::shared_runtime::SharedRuntimePy;

/// An opaque, registered metric context handle returned by
/// [`TelemetryWorkerPy::register_metric_context`] and passed back to
/// [`TelemetryWorkerPy::add_point`]. Caching this on the Python side lets the hot add path
/// skip re-marshalling the metric name/tags (and the register-or-lookup) on every point.
///
/// A `ContextKey` is only valid for the worker that produced it, so the Python cache is
/// cleared whenever the worker is rebuilt (fork, test-token/payload-file reconfigure).
#[pyclass(frozen, name = "MetricContext")]
pub struct MetricContextPy(ContextKey);

/// Metric namespace, exposed to Python as `MetricNamespace.<Variant>`.
#[pyclass(eq, hash, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Hash, ConvertToPyO3Enum)]
pub struct MetricNamespace(pub MetricNamespaceNative);

/// Metric type, exposed to Python as `MetricType.<Variant>`.
#[pyclass(eq, hash, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Hash, ConvertToPyO3Enum)]
pub struct MetricType(pub MetricTypeNative);

/// Configuration origin, exposed to Python as `ConfigurationOrigin.<Variant>`.
#[pyclass(eq, hash, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Hash, ConvertToPyO3Enum)]
pub struct ConfigurationOrigin(pub ConfigurationOriginNative);

/// Log level, exposed to Python as `LogLevel.<Variant>`.
#[pyclass(eq, hash, frozen, from_py_object)]
#[derive(Clone, Copy, PartialEq, Eq, Hash, ConvertToPyO3Enum)]
pub struct LogLevel(pub LogLevelNative);

/// Parse tags. Invalid tags are dropped.
fn parse_tag_list(tags: &[String]) -> Vec<Tag> {
    let mut out = Vec::with_capacity(tags.len());
    for t in tags {
        let (parsed, _err) = parse_tags(t);
        out.extend(parsed);
    }
    out
}

/// Best-effort delivery: telemetry submission must never raise into the tracer.
/// A full channel (backpressure) or a stopped worker simply drops the datum with
/// a debug log, matching the previous fire-and-forget Python behaviour.
fn drop_on_err(what: &str, r: anyhow::Result<()>) {
    if let Err(e) = r {
        tracing::debug!("telemetry: dropped {what}: {e}");
    }
}

#[pyclass(name = "TelemetryWorker")]
pub struct TelemetryWorkerPy {
    // The worker's mailbox handle. Built via `build_worker(None)`, so it holds NO tokio
    // runtime `Handle`: the worker task is owned by the SharedRuntime, which pauses/drops it
    // across fork. The inherited handle can thus be safely dropped in forks.
    handle: TelemetryWorkerHandle<NativeCapabilities>,
    shared_runtime: Arc<ForkSafeRuntime>,
    // Held to keep the worker registered on the SharedRuntime; dropping it
    // would leak the worker until the runtime shuts down. `stop()` consumes it.
    worker_handle: Mutex<Option<WorkerHandle>>,
}

#[pymethods]
impl TelemetryWorkerPy {
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (
        runtime,
        *,
        service,
        env,
        app_version,
        language_name,
        language_version,
        tracer_version,
        runtime_id,
        runtime_name,
        runtime_version,
        process_tags,
        hostname,
        os,
        os_version,
        architecture,
        kernel_name,
        kernel_release,
        kernel_version,
        container_id,
        endpoint_url,
        api_key,
        session_id,
        parent_session_id,
        root_session_id,
        heartbeat_interval_secs,
        extended_heartbeat_interval_secs,
        debug_enabled,
        emit_app_lifecycle = true,
        endpoints_message_limit = 300,
        test_session_token = None,
        install_id = None,
        install_type = None,
        install_time = None,
    ))]
    fn new(
        runtime: PyRef<'_, SharedRuntimePy>,
        service: String,
        env: Option<String>,
        app_version: Option<String>,
        language_name: String,
        language_version: String,
        tracer_version: String,
        runtime_id: String,
        runtime_name: Option<String>,
        runtime_version: Option<String>,
        process_tags: Option<String>,
        hostname: String,
        os: Option<String>,
        os_version: Option<String>,
        architecture: Option<String>,
        kernel_name: Option<String>,
        kernel_release: Option<String>,
        kernel_version: Option<String>,
        container_id: Option<String>,
        endpoint_url: String,
        api_key: Option<String>,
        session_id: String,
        parent_session_id: Option<String>,
        root_session_id: Option<String>,
        heartbeat_interval_secs: f64,
        extended_heartbeat_interval_secs: f64,
        debug_enabled: bool,
        emit_app_lifecycle: bool,
        endpoints_message_limit: u32,
        test_session_token: Option<String>,
        install_id: Option<String>,
        install_type: Option<String>,
        install_time: Option<String>,
    ) -> PyResult<Self> {
        let shared_runtime = runtime.as_arc().clone();

        let mut builder = TelemetryWorkerBuilder::new(
            hostname.clone(),
            service,
            language_name,
            language_version,
            tracer_version,
        );

        builder.flavor = TelemetryWorkerFlavor::Full;
        builder.runtime_id = Some(runtime_id);

        builder.application.service_version = app_version;
        builder.application.env = env;
        builder.application.runtime_name = runtime_name;
        builder.application.runtime_version = runtime_version;
        builder.application.process_tags = process_tags;

        builder.host = Host {
            hostname,
            container_id,
            os,
            os_version,
            architecture,
            kernel_name,
            kernel_release,
            kernel_version,
        };

        let mut config = Config::default();
        config.telemetry_heartbeat_interval = Duration::from_secs_f64(heartbeat_interval_secs);
        config.telemetry_extended_heartbeat_interval =
            Duration::from_secs_f64(extended_heartbeat_interval_secs);
        config.debug_enabled = debug_enabled;
        config.telemetry_debug_logging_enabled = debug_enabled;
        config.session_id = Some(session_id);
        config.parent_session_id = parent_session_id;
        config.root_session_id = root_session_id;
        // Forked children pass false so they heartbeat without re-emitting app-started/closing
        config.emit_app_lifecycle = emit_app_lifecycle;
        config.endpoints_message_limit = endpoints_message_limit;

        config.direct_submission_enabled = api_key.is_some();
        config
            .set_endpoint(TelemetryEndpoint {
                url: Some(endpoint_url),
                api_key,
                test_token: test_session_token,
                ..Default::default()
            })
            .map_err(|e| PyValueError::new_err(format!("invalid telemetry endpoint: {e}")))?;

        builder.config = config;

        if install_id.is_some() || install_type.is_some() || install_time.is_some() {
            builder.install_signature = Some(data::InstallSignature {
                install_id,
                install_type,
                install_time,
            });
        }

        // Spawning the worker on shared runtime with `restart_on_fork = false` so that children
        // can safely drop it fully without emitting app-closing.
        let (handle, worker) = builder.build_worker::<NativeCapabilities>(None);
        let worker_handle = shared_runtime
            .spawn_worker(worker, false)
            .map_err(|e| PyValueError::new_err(format!("failed to spawn telemetry worker: {e}")))?;

        Ok(TelemetryWorkerPy {
            handle,
            shared_runtime,
            worker_handle: Mutex::new(Some(worker_handle)),
        })
    }

    /// Send the app-started lifecycle event. Call ONCE, on the origin process.
    fn start(&self) -> PyResult<()> {
        self.handle
            .send_start()
            .map_err(|e| PyValueError::new_err(format!("failed to start telemetry worker: {e}")))
    }

    /// Flush + optionally emit app-closing (in origin process), then tear the worker down.
    fn stop(&self, py: Python<'_>, send_app_closing: bool) -> PyResult<()> {
        // Take the registration handle; if already stopped, nothing to do.
        let worker_handle = self
            .worker_handle
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take();

        if send_app_closing {
            // Flush the app-closing batch first (clears data.started).
            let _ = self.handle.send_stop();
        } else {
            let _ = self.flush(py);
        }
        if let Some(wh) = worker_handle {
            // Release the GIL while the async teardown runs on the shared
            // runtime. `wh.stop()` pauses the worker then shuts it down.
            py.detach(|| {
                let _ = self.shared_runtime.block_on(async {
                    let _ = wh.stop().await;
                });
            });
        }
        Ok(())
    }

    /// Force a data flush without emitting any lifecycle event, and BLOCK until the
    /// worker has processed it — matching the old Python writer's synchronous
    /// `periodic(force_flush=True)`. The stats request sits behind the FlushData in the
    /// FIFO mailbox, so receiving its reply proves the flush completed; and because the
    /// worker awaits the flush's HTTP POST inside the FlushData handler, the data has
    /// reached the agent by the time this returns. This makes test assertions (and
    /// shutdown flushes) deterministic instead of racing the async send.
    fn flush(&self, py: Python<'_>) -> PyResult<()> {
        // Aggregate the current interval's metric points into series FIRST
        // (FlushMetricAggr), otherwise FlushData would send no metrics — the
        // worker normally does this on its own 10s cadence, but a forced flush
        // must do it inline so metrics added since the last cadence are sent.
        py.detach(|| {
            let _ = self.shared_runtime.block_on(async {
                let _ = self
                    .handle
                    .send_msg(TelemetryActions::Lifecycle(
                        LifecycleAction::FlushMetricAggr,
                    ))
                    .await;
                let _ = self
                    .handle
                    .send_msg(TelemetryActions::Lifecycle(LifecycleAction::FlushData))
                    .await;
                // Queued after the flushes, to ensure processing finished.
                if let Ok(receiver) = self.handle.stats() {
                    let _ = receiver.await;
                }
            });
        });
        Ok(())
    }

    #[pyo3(signature = (name, value, origin, config_id, seq_id))]
    fn add_configuration(
        &self,
        name: String,
        value: Option<String>,
        origin: ConfigurationOrigin,
        config_id: Option<String>,
        seq_id: u64,
    ) -> PyResult<()> {
        drop_on_err(
            "configuration",
            self.handle
                .try_send_msg(TelemetryActions::AddConfig(data::Configuration {
                    name,
                    value,
                    origin: origin.0,
                    config_id,
                    seq_id: Some(seq_id),
                })),
        );
        Ok(())
    }

    #[pyo3(signature = (name, version, enabled, compatible, auto_enabled, error=None))]
    fn add_integration(
        &self,
        name: String,
        version: Option<String>,
        enabled: bool,
        compatible: Option<bool>,
        auto_enabled: Option<bool>,
        error: Option<String>,
    ) -> PyResult<()> {
        drop_on_err(
            "integration",
            self.handle
                .add_integration(name, enabled, version, compatible, auto_enabled, error),
        );
        Ok(())
    }

    /// `metadata` is the SCA metadata list as `(type, value)` pairs, where `value` is an
    /// opaque stringified-JSON payload (per the `dependency_metadata` telemetry schema),
    /// passed through verbatim.
    #[pyo3(signature = (name, version, metadata))]
    fn add_dependency(
        &self,
        name: String,
        version: Option<String>,
        metadata: Option<Vec<(String, String)>>,
    ) -> PyResult<()> {
        let metadata: Option<Vec<DependencyMetadata>> = metadata.map(|items| {
            items
                .into_iter()
                .map(|(r#type, value)| DependencyMetadata { r#type, value })
                .collect()
        });
        drop_on_err(
            "dependency",
            self.handle.add_dependency(name, version, metadata),
        );
        Ok(())
    }

    /// `identifier` is the Python-computed dedup key (passed through as the
    /// `LogIdentifier`). `tags` is a pre-formatted tag string (or `None`).
    ///
    /// Sent as a raw `AddLog` action (not the handle's `add_log`, which both
    /// hashes the identifier and hardcodes empty tags) so the dedup key and tags
    /// survive verbatim.
    #[pyo3(signature = (identifier, message, level, stack_trace, tags))]
    fn add_log(
        &self,
        identifier: u64,
        message: String,
        level: LogLevel,
        stack_trace: Option<String>,
        tags: Option<String>,
    ) -> PyResult<()> {
        drop_on_err(
            "log",
            self.handle.try_send_msg(TelemetryActions::AddLog((
                libdd_telemetry::worker::LogIdentifier { identifier },
                data::Log {
                    message,
                    level: level.0,
                    stack_trace,
                    count: 1,
                    tags: tags.unwrap_or_default(),
                    is_sensitive: false,
                    is_crash: false,
                },
            ))),
        );
        Ok(())
    }

    /// Register a metric context for `(namespace, name, type, tags)` and return an opaque
    /// handle. Call ONCE per unique metric (the caller — the Python writer — caches the
    /// handle); calling twice for the same metric registers a duplicate context. The hot
    /// add path then uses [`add_point`], skipping the per-point name/tag marshalling.
    #[pyo3(signature = (namespace, name, metric_type, tags, common))]
    fn register_metric_context(
        &self,
        namespace: MetricNamespace,
        name: String,
        metric_type: MetricType,
        tags: Vec<String>,
        common: bool,
    ) -> MetricContextPy {
        let parsed_tags = parse_tag_list(&tags);
        let key = self.handle.register_metric_context(
            name,
            parsed_tags,
            metric_type.0,
            common,
            namespace.0,
        );
        MetricContextPy(key)
    }

    /// Add `value` to a metric context previously returned by [`register_metric_context`].
    /// Cheap hot path: the point is published to the worker's lock-free ring buffer.
    fn add_point(&self, context: &MetricContextPy, value: f64) {
        drop_on_err(
            "metric point",
            self.handle.add_point(value, &context.0, Vec::new()),
        );
    }

    /// Like [`add_point`], but allowing for explicit tags as well. To be used when the
    /// cardinality of tags is unknown.
    fn add_point_with_tags(&self, context: &MetricContextPy, value: f64, tags: Vec<String>) {
        drop_on_err(
            "metric point",
            self.handle
                .add_point(value, &context.0, parse_tag_list(&tags)),
        );
    }

    #[pyo3(signature = (product, enabled, version))]
    fn add_product_change(
        &self,
        product: String,
        enabled: bool,
        version: Option<String>,
    ) -> PyResult<()> {
        drop_on_err(
            "product change",
            self.handle.add_product_change(product, enabled, version),
        );
        Ok(())
    }

    /// Report an instrumented endpoint (ASM app-endpoints). `method`/`path` are
    /// optional; `operation_name`/`resource_name` default to empty strings.
    /// `request_body_type`/`response_body_type` carry the declared request/response media types
    /// and `response_code` the declared status codes (API Security inventory); all default to
    /// empty.
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (method, path, operation_name, resource_name, request_body_type=None, response_body_type=None, response_code=None))]
    fn add_endpoint(
        &self,
        method: String,
        path: String,
        operation_name: Option<String>,
        resource_name: Option<String>,
        request_body_type: Option<Vec<String>>,
        response_body_type: Option<Vec<String>>,
        response_code: Option<Vec<u32>>,
    ) -> PyResult<()> {
        // ``method`` and ``path`` are Option in libdatadog, but the backend always expects string.
        let endpoint = data::Endpoint {
            method: Some(parse_method(&method)),
            path: Some(path),
            operation_name: operation_name.unwrap_or_default(),
            resource_name: resource_name.unwrap_or_default(),
            request_body_type,
            response_body_type,
            response_code,
        };
        drop_on_err(
            "endpoint",
            self.handle
                .try_send_msg(TelemetryActions::AddEndpoint(endpoint)),
        );
        Ok(())
    }
}

/// Best-effort HTTP method mapping for `app-endpoints`. Unknown methods map to
/// `Other` ("*").
fn parse_method(method: &str) -> data::Method {
    use data::Method::*;
    match method.to_ascii_uppercase().as_str() {
        "GET" => Get,
        "POST" => Post,
        "PUT" => Put,
        "DELETE" => Delete,
        "PATCH" => Patch,
        "HEAD" => Head,
        "OPTIONS" => Options,
        "TRACE" => Trace,
        "CONNECT" => Connect,
        _ => Other,
    }
}

impl TelemetryWorkerPy {
    pub(crate) fn clone_handle(&self) -> TelemetryWorkerHandle<NativeCapabilities> {
        self.handle.clone()
    }
}

pub fn register_telemetry(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TelemetryWorkerPy>()?;
    m.add_class::<MetricContextPy>()?;
    MetricNamespace::register(m)?;
    MetricType::register(m)?;
    ConfigurationOrigin::register(m)?;
    LogLevel::register(m)?;
    Ok(())
}
