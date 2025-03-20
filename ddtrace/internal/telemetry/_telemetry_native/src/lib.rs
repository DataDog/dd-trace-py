use ddcommon::{tag::Tag, config::parse_env};
use ddtelemetry::{data, metrics, worker};
use futures::executor::block_on;
use pyo3::Bound;
use pyo3::PyTypeInfo;
use pyo3::prelude::*;
use std::{
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
    env,
};

// Python-friendly Config wrapper with all fields as Option
#[pyclass]
#[derive(Clone, Debug)]
pub struct PyConfig {
    #[pyo3(get, set)]
    pub endpoint: Option<String>,              // Maps to DD_TRACE_AGENT_URL
    #[pyo3(get, set)]
    pub telemetry_debug_logging_enabled: Option<bool>, // Maps to _DD_SHARED_LIB_DEBUG
    #[pyo3(get, set)]
    pub telemetry_heartbeat_interval: Option<f64>, // Maps to DD_TELEMETRY_HEARTBEAT_INTERVAL
    #[pyo3(get, set)]
    pub direct_submission_enabled: Option<bool>, // Maps to _DD_DIRECT_SUBMISSION_ENABLED
    // restartable omitted as it's always false in Config::from_settings
}

#[pymethods]
impl PyConfig {
    #[new]
    #[pyo3(signature = (
        endpoint = "".to_string(),
        telemetry_debug_logging_enabled = false,
        telemetry_heartbeat_interval = 60.0,
        direct_submission_enabled = false
    ))]
    fn new(
        endpoint: Option<String>,
        telemetry_debug_logging_enabled: Option<bool>,
        telemetry_heartbeat_interval: Option<f64>,
        direct_submission_enabled: Option<bool>,
    ) -> Self {
        PyConfig {
            endpoint,
            telemetry_debug_logging_enabled,
            telemetry_heartbeat_interval,
            direct_submission_enabled,
        }
    }
}


#[pyclass]
struct NativeTelemetryWorker {
    handle: worker::TelemetryWorkerHandle,
    started: AtomicBool,
}

#[pyclass]
#[derive(Debug)]
enum PyLogLevel {
    Error,
    Warn,
    Debug,
}

impl From<PyLogLevel> for data::LogLevel {
    fn from(level: PyLogLevel) -> Self {
        match level {
            PyLogLevel::Error => data::LogLevel::Error,
            PyLogLevel::Warn => data::LogLevel::Warn,
            PyLogLevel::Debug => data::LogLevel::Debug,
        }
    }
}

// Wrap MetricBucketStats for Python
#[pyclass]
#[derive(Debug, Clone)]
pub struct PyMetricBucketStats {
    #[pyo3(get)]
    pub buckets: u32,
    #[pyo3(get)]
    pub series: u32,
    #[pyo3(get)]
    pub series_points: u32,
    #[pyo3(get)]
    pub distributions: u32,
    #[pyo3(get)]
    pub distributions_points: u32,
}

impl From<metrics::MetricBucketStats> for PyMetricBucketStats {
    fn from(stats: metrics::MetricBucketStats) -> Self {
        PyMetricBucketStats {
            buckets: stats.buckets,
            series: stats.series,
            series_points: stats.series_points,
            distributions: stats.distributions,
            distributions_points: stats.distributions_points,
        }
    }
}
// Wrap TelemetryWorkerStats for Python
#[pyclass]
#[derive(Debug, Clone)]
pub struct PyTelemetryWorkerStats {
    #[pyo3(get)]
    pub dependencies_stored: u32,
    #[pyo3(get)]
    pub dependencies_unflushed: u32,
    #[pyo3(get)]
    pub configurations_stored: u32,
    #[pyo3(get)]
    pub configurations_unflushed: u32,
    #[pyo3(get)]
    pub integrations_stored: u32,
    #[pyo3(get)]
    pub integrations_unflushed: u32,
    #[pyo3(get)]
    pub logs: u32,
    #[pyo3(get)]
    pub metric_contexts: u32,
    #[pyo3(get)]
    pub metric_buckets: PyMetricBucketStats,
}

impl From<worker::TelemetryWorkerStats> for PyTelemetryWorkerStats {
    fn from(stats: worker::TelemetryWorkerStats) -> Self {
        PyTelemetryWorkerStats {
            dependencies_stored: stats.dependencies_stored,
            dependencies_unflushed: stats.dependencies_unflushed,
            configurations_stored: stats.configurations_stored,
            configurations_unflushed: stats.configurations_unflushed,
            integrations_stored: stats.integrations_stored,
            integrations_unflushed: stats.integrations_unflushed,
            logs: stats.logs,
            metric_contexts: stats.metric_contexts,
            metric_buckets: PyMetricBucketStats::from(stats.metric_buckets),
        }
    }
}

// Wrap ContextKey for Python
#[pyclass]
#[derive(Debug, Clone)]
struct PyContextKey {
    inner: metrics::ContextKey,
}

impl From<metrics::ContextKey> for PyContextKey {
    fn from(key: metrics::ContextKey) -> Self {
        PyContextKey { inner: key }
    }
}

impl PyContextKey {
    fn to_rust(&self) -> metrics::ContextKey {
        self.inner
    }
}

// Wrap MetricType for Python
#[pyclass]
#[derive(Debug, Clone)]
enum PyMetricType {
    Gauge,
    Count,
    Distribution,
}

impl From<data::metrics::MetricType> for PyMetricType {
    fn from(mt: data::metrics::MetricType) -> Self {
        match mt {
            data::metrics::MetricType::Gauge => PyMetricType::Gauge,
            data::metrics::MetricType::Count => PyMetricType::Count,
            data::metrics::MetricType::Distribution => PyMetricType::Distribution,
        }
    }
}

impl PyMetricType {
    fn to_rust(&self) -> data::metrics::MetricType {
        match self {
            PyMetricType::Gauge => data::metrics::MetricType::Gauge,
            PyMetricType::Count => data::metrics::MetricType::Count,
            PyMetricType::Distribution => data::metrics::MetricType::Distribution,
        }
    }
}

// Wrap MetricNamespace for Python
#[pyclass]
#[derive(Debug, Clone)]
enum PyMetricNamespace {
    Tracers,
    Profilers,
    Rum,
    Appsec,
    IdePlugins,
    LiveDebugger,
    Iast,
    General,
    Telemetry,
    Apm,
    Sidecar,
}

impl From<PyMetricNamespace> for data::metrics::MetricNamespace {
    fn from(ns: PyMetricNamespace) -> Self {
        match ns {
            PyMetricNamespace::Tracers => data::metrics::MetricNamespace::Tracers,
            PyMetricNamespace::Profilers => data::metrics::MetricNamespace::Profilers,
            PyMetricNamespace::Rum => data::metrics::MetricNamespace::Rum,
            PyMetricNamespace::Appsec => data::metrics::MetricNamespace::Appsec,
            PyMetricNamespace::IdePlugins => data::metrics::MetricNamespace::IdePlugins,
            PyMetricNamespace::LiveDebugger => data::metrics::MetricNamespace::LiveDebugger,
            PyMetricNamespace::Iast => data::metrics::MetricNamespace::Iast,
            PyMetricNamespace::General => data::metrics::MetricNamespace::General,
            PyMetricNamespace::Telemetry => data::metrics::MetricNamespace::Telemetry,
            PyMetricNamespace::Apm => data::metrics::MetricNamespace::Apm,
            PyMetricNamespace::Sidecar => data::metrics::MetricNamespace::Sidecar,
        }
    }
}

// Internal helper function to convert Python tags to Rust Tags
fn pytags2tags(tags: Option<Vec<(String, String)>>) -> PyResult<Vec<Tag>> {
    tags.unwrap_or_default() // None -> empty Vec
        .into_iter()
        .map(|(key, value)| {
            Tag::new(key, value).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Invalid tag: {}", e))
            })
        })
        .collect::<PyResult<Vec<_>>>()
}

#[pymethods]
impl NativeTelemetryWorker {
    #[new]
    #[pyo3(signature = (
        host,
        service,
        config,
        language_version = "3.12".to_string(),
        tracer_version = "3.1".to_string()
    ))]
    fn new(
        host: String,
        service: String,
        config: PyConfig,
        language_version: String,
        tracer_version: String,
    ) -> PyResult<Self> {

        // If the PyConfig fields are not None, override the environment variables.
        // If None, use the env vars with some translations.
        if let Some(interval) = config.telemetry_heartbeat_interval {
            if interval < 0.0 {
                return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                    "Telemetry heartbeat interval must be non-negative",
                ));
            }
            unsafe {
                env::set_var("DD_TELEMETRY_HEARTBEAT_INTERVAL", interval.to_string());
            }
        }

        if let Some(endpoint) = config.endpoint {
            if !endpoint.is_empty() {
                unsafe {
                    env::set_var("DD_TRACE_AGENT_URL", endpoint);
                }
            }
        }
        if let Some(debug_enabled) = config.telemetry_debug_logging_enabled {
            unsafe {
                env::set_var("_DD_SHARED_LIB_DEBUG", if debug_enabled { "1" } else { "0" });
            }
        } else if parse_env::bool("DD_TRACE_DEBUG") == Some(true) {
            unsafe {
                env::set_var("_DD_SHARED_LIB_DEBUG", "1");
            }
        }

        if let Some(direct_submission) = config.direct_submission_enabled {
            unsafe {
                env::set_var("_DD_DIRECT_SUBMISSION_ENABLED", if direct_submission { "1" } else { "0" });
            }
        }

        let builder = worker::TelemetryWorkerBuilder::new(
            host,
            service,
            "python".into(),
            language_version,
            tracer_version,
        );

        let handle = builder.run().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Failed to run telemetry worker: {}",
                e
            ))
        })?;

        Ok(NativeTelemetryWorker {
            handle,
            started: AtomicBool::new(false),
        })
    }

    fn send_start(&self) -> PyResult<()> {
        self.handle.send_start().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Failed to send start: {}",
                e
            ))
        })?;
        self.started.store(true, Ordering::SeqCst);
        Ok(())
    }

    fn wait_for_shutdown(&self) {
        self.handle.wait_for_shutdown();
    }

    fn wait_for_shutdown_deadline(&self, timeout_secs: f64) {
        let deadline = Instant::now() + Duration::from_secs_f64(timeout_secs);
        self.handle.wait_for_shutdown_deadline(deadline);
    }

    fn send_stop(&self) -> PyResult<()> {
        self.handle.send_stop().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!("Failed to send stop: {}", e))
        })?;
        self.started.store(false, Ordering::SeqCst);
        Ok(())
    }

    fn reset(&self) -> PyResult<()> {
        // We don't have a direct way to reset the queues but restarting the worker has the sa
        if self.started() {
            self.send_stop()?;
        }
        self.send_start()?;
        Ok(())
    }

    fn started(&self) -> bool {
        self.started.load(Ordering::SeqCst)
    }

    fn add_log(
        &self,
        identifier: String,
        message: String,
        level: i32,
        stack_trace: Option<String>,
    ) -> PyResult<()> {
        let log_level = match level {
            0 => data::LogLevel::Error,
            1 => data::LogLevel::Warn,
            2 => data::LogLevel::Debug,
            _ => {
                return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                    "Invalid log level; use 0-2",
                ));
            }
        };
        self.handle
            .add_log(identifier, message, log_level, stack_trace)
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                    "Failed to add log: {}",
                    e
                ))
            })?;
        Ok(())
    }

    fn add_dependency(&self, name: String, version: Option<String>) -> PyResult<()> {
        self.handle.add_dependency(name, version).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Failed to add dependency: {}",
                e
            ))
        })?;
        Ok(())
    }

    fn add_integration(
        &self,
        name: String,
        enabled: bool,
        version: Option<String>,
        compatible: Option<bool>,
        auto_enabled: Option<bool>,
    ) -> PyResult<()> {
        self.handle
            .add_integration(name, enabled, version, compatible, auto_enabled)
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                    "Failed to add integration: {}",
                    e
                ))
            })?;
        Ok(())
    }

    fn register_metric_context(
        &self,
        name: String,
        tags: Option<Vec<(String, String)>>, // Optional tags
        metric_type: PyMetricType,
        common: bool,
        namespace: PyMetricNamespace,
    ) -> PyResult<PyContextKey> {
        let rust_tags = pytags2tags(tags)?;
        let context_key = self.handle.register_metric_context(
            name,
            rust_tags,
            metric_type.to_rust(),
            common,
            namespace.into(),
        );
        Ok(PyContextKey::from(context_key))
    }

    fn add_point(
        &self,
        value: f64,
        context: PyContextKey,
        extra_tags: Option<Vec<(String, String)>>,
    ) -> PyResult<()> {
        let rust_context = context.to_rust();
        let tags: Vec<Tag> = pytags2tags(extra_tags)?;
        self.handle
            .add_point(value, &rust_context, tags)
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                    "Failed to add point: {}",
                    e
                ))
            })?;
        Ok(())
    }

    fn add_config(&self, name: String, value: String, origin: String) -> PyResult<()> {
        // Convert origin to ConfigurationOrigin
        let configuration_origin = match origin.to_lowercase().replace('_', "").as_str() {
            "envvar" => data::ConfigurationOrigin::EnvVar,
            "code" => data::ConfigurationOrigin::Code,
            "ddconfig" => data::ConfigurationOrigin::DdConfig,
            "remoteconfig" => data::ConfigurationOrigin::RemoteConfig,
            "default" => data::ConfigurationOrigin::Default,
            "datadog" => data::ConfigurationOrigin::DdConfig, // Additional rule
            "env" => data::ConfigurationOrigin::EnvVar,       // Additional rule
            _ => data::ConfigurationOrigin::Default,          // Catch-all
        };

        self.handle
            .try_send_msg(worker::TelemetryActions::AddConfig(data::Configuration {
                name,
                value,
                origin: configuration_origin,
            }))
            .map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                    "Failed to send config: {}",
                    e
                ))
            })?;
        Ok(())
    }

    // TODO(maybe): async version like the original libdatadog is.
    fn stats(&self) -> PyResult<PyTelemetryWorkerStats> {
        let receiver = self.handle.stats().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Failed to request stats: {}",
                e
            ))
        })?;
        let stats = block_on(receiver).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Failed to receive stats: {}",
                e
            ))
        })?;
        Ok(PyTelemetryWorkerStats::from(stats))
    }
}

/// Python module definition.
#[pymodule]
fn _native_telemetry(_py: Python<'_>, m: Bound<'_, PyModule>) -> PyResult<()> {
    m.add(
        "NativeTelemetryWorker",
        NativeTelemetryWorker::type_object(_py),
    )?;
    m.add("PyLogLevel", PyLogLevel::type_object(_py))?;
    m.add("PyMetricType", PyMetricType::type_object(_py))?;
    m.add("PyMetricNamespace", PyMetricNamespace::type_object(_py))?;
    m.add("PyContextKey", PyContextKey::type_object(_py))?;
    m.add("PyMetricBucketStats", PyMetricBucketStats::type_object(_py))?;
    m.add("PyConfig", PyConfig::type_object(_py))?;
    m.add(
        "PyTelemetryWorkerStats",
        PyTelemetryWorkerStats::type_object(_py),
    )?;
    Ok(())
}
