use libdd_otel_telemetry::{
    parse_otlp_headers, InstrumentDescriptor, InstrumentId, InstrumentKind, ObservableCallback,
    ObservableMeasurement, OtelMetricsAggregator, OtelMetricsAggregatorBuilder, OtlpExporterConfig,
    OtlpProtocol, ResourceBuilder, Temporality,
};
use pyo3::{
    exceptions::{PyRuntimeError, PyTypeError, PyValueError},
    prelude::*,
    types::PyAny,
};
use std::{
    ffi::c_int,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};

#[cfg(all(not(Py_3_13), not(PyPy), not(GraalPy)))]
unsafe extern "C" {
    fn _Py_IsFinalizing() -> c_int;
}

#[cfg(Py_3_13)]
fn python_is_finalizing() -> bool {
    unsafe { pyo3::ffi::Py_IsFinalizing() != 0 }
}

#[cfg(all(not(Py_3_13), not(PyPy), not(GraalPy)))]
fn python_is_finalizing() -> bool {
    unsafe { _Py_IsFinalizing() != 0 }
}

#[cfg(any(PyPy, GraalPy))]
fn python_is_finalizing() -> bool {
    false
}

fn adapt_python_callback(
    callback: Py<PyAny>,
    callbacks_enabled: Arc<AtomicBool>,
) -> ObservableCallback {
    Arc::new(move || {
        if !callbacks_enabled.load(Ordering::Acquire) || python_is_finalizing() {
            return Vec::new();
        }
        Python::try_attach(|py| {
            match callback
                .call0(py)
                .and_then(|result| result.extract::<Vec<(f64, Vec<(String, String)>)>>(py))
            {
                Ok(measurements) => measurements
                    .into_iter()
                    .map(|(value, attributes)| ObservableMeasurement::new(value, attributes))
                    .collect(),
                Err(error) => {
                    error.write_unraisable(py, Some(callback.bind(py)));
                    Vec::new()
                }
            }
        })
        .unwrap_or_default()
    })
}

fn parse_protocol(protocol: &str) -> PyResult<OtlpProtocol> {
    OtlpProtocol::from_config_str(protocol)
        .ok_or_else(|| PyValueError::new_err(format!("Invalid OTLP protocol: {protocol}")))
}

fn parse_instrument_kind(kind: &str) -> PyResult<InstrumentKind> {
    match kind {
        "counter" => Ok(InstrumentKind::Counter),
        "up_down_counter" => Ok(InstrumentKind::UpDownCounter),
        "histogram" => Ok(InstrumentKind::Histogram),
        "observable_gauge" => Ok(InstrumentKind::ObservableGauge),
        "observable_counter" => Ok(InstrumentKind::ObservableCounter),
        "observable_up_down_counter" => Ok(InstrumentKind::ObservableUpDownCounter),
        other => Err(PyValueError::new_err(format!(
            "Invalid instrument kind: {other}"
        ))),
    }
}

/// Builds the Rust metrics provider from primitive Python configuration.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn build_otel_metrics_provider(
    service: Option<&str>,
    env: Option<&str>,
    version: Option<&str>,
    resource_attributes: Vec<(String, String)>,
    endpoint: &str,
    protocol: &str,
    timeout_ms: u64,
    headers: &str,
    temporality: &str,
    export_interval_ms: u64,
) -> PyResult<(OtelMetricsProviderPy, Vec<String>)> {
    let mut resource = ResourceBuilder::new();
    if let Some(service) = service {
        resource = resource.with_service(service);
    }
    if let Some(env) = env {
        resource = resource.with_env(env);
    }
    if let Some(version) = version {
        resource = resource.with_version(version);
    }
    for (key, value) in resource_attributes {
        resource = resource.with_attribute(key, value);
    }

    let protocol = parse_protocol(protocol)?;
    let mut exporter =
        OtlpExporterConfig::new(endpoint, protocol).with_timeout(Duration::from_millis(timeout_ms));
    for (key, value) in parse_otlp_headers(headers) {
        exporter = exporter.with_header(key, value);
    }

    let builder = OtelMetricsAggregatorBuilder::new()
        .with_resource(resource)
        .with_metrics_exporter(exporter)
        .with_metrics_temporality(Temporality::from_config_str(temporality))
        .with_export_interval(Duration::from_millis(export_interval_ms));
    let (provider, warnings) = builder.build_with_default_runtime();
    let warnings = warnings.iter().map(ToString::to_string).collect();
    Ok((
        OtelMetricsProviderPy {
            inner: Some(provider),
            callbacks_enabled: Arc::new(AtomicBool::new(true)),
        },
        warnings,
    ))
}

/// A slim Python handle to the Rust-owned metrics provider.
#[pyclass(name = "OtelMetricsProvider")]
pub struct OtelMetricsProviderPy {
    inner: Option<OtelMetricsAggregator>,
    callbacks_enabled: Arc<AtomicBool>,
}

impl OtelMetricsProviderPy {
    fn try_as_ref(&self) -> PyResult<&OtelMetricsAggregator> {
        self.inner.as_ref().ok_or(PyValueError::new_err(
            "OtelMetricsProvider has already been shut down",
        ))
    }
}

#[pymethods]
impl OtelMetricsProviderPy {
    /// `kind` is one of `"counter"`, `"up_down_counter"`, `"histogram"`, `"observable_gauge"`,
    /// `"observable_counter"`, `"observable_up_down_counter"`.
    #[allow(clippy::too_many_arguments)]
    fn register_instrument(
        &self,
        name: &str,
        kind: &str,
        unit: Option<&str>,
        description: Option<&str>,
        meter_name: &str,
        meter_version: Option<&str>,
        meter_schema_url: Option<&str>,
    ) -> PyResult<u64> {
        let kind = parse_instrument_kind(kind)?;
        let mut descriptor = InstrumentDescriptor::new(name, kind).with_scope(
            meter_name,
            meter_version.map(str::to_string),
            meter_schema_url.map(str::to_string),
        );
        if let Some(unit) = unit {
            descriptor = descriptor.with_unit(unit);
        }
        if let Some(description) = description {
            descriptor = descriptor.with_description(description);
        }
        Ok(self.try_as_ref()?.register_instrument(descriptor).0)
    }

    #[allow(clippy::too_many_arguments)]
    fn register_observable_instrument(
        &self,
        py: Python<'_>,
        name: &str,
        kind: &str,
        unit: Option<&str>,
        description: Option<&str>,
        meter_name: &str,
        meter_version: Option<&str>,
        meter_schema_url: Option<&str>,
        callback: Py<PyAny>,
    ) -> PyResult<u64> {
        if !callback.bind(py).is_callable() {
            return Err(PyTypeError::new_err("callback must be callable"));
        }
        let kind = parse_instrument_kind(kind)?;
        let mut descriptor = InstrumentDescriptor::new(name, kind).with_scope(
            meter_name,
            meter_version.map(str::to_string),
            meter_schema_url.map(str::to_string),
        );
        if let Some(unit) = unit {
            descriptor = descriptor.with_unit(unit);
        }
        if let Some(description) = description {
            descriptor = descriptor.with_description(description);
        }
        let callback = adapt_python_callback(callback, Arc::clone(&self.callbacks_enabled));
        Ok(self
            .try_as_ref()?
            .register_observable_instrument(descriptor, callback)
            .0)
    }

    fn record_counter(
        &self,
        py: Python<'_>,
        id: u64,
        value: f64,
        attrs: Vec<(String, String)>,
    ) -> PyResult<()> {
        let aggregator = self.try_as_ref()?;
        py.detach(|| aggregator.record_counter(InstrumentId(id), value, &attrs));
        Ok(())
    }

    fn record_up_down_counter(
        &self,
        py: Python<'_>,
        id: u64,
        value: f64,
        attrs: Vec<(String, String)>,
    ) -> PyResult<()> {
        let aggregator = self.try_as_ref()?;
        py.detach(|| aggregator.record_up_down_counter(InstrumentId(id), value, &attrs));
        Ok(())
    }

    fn record_histogram(
        &self,
        py: Python<'_>,
        id: u64,
        value: f64,
        attrs: Vec<(String, String)>,
    ) -> PyResult<()> {
        let aggregator = self.try_as_ref()?;
        py.detach(|| aggregator.record_histogram(InstrumentId(id), value, &attrs));
        Ok(())
    }

    fn observe_gauge(
        &self,
        py: Python<'_>,
        id: u64,
        value: f64,
        attrs: Vec<(String, String)>,
    ) -> PyResult<()> {
        let aggregator = self.try_as_ref()?;
        py.detach(|| aggregator.observe_gauge(InstrumentId(id), value, &attrs));
        Ok(())
    }

    /// Returns `(metrics_export_attempts, metrics_export_successes, metrics_export_failures)`.
    fn export_counters(&self) -> PyResult<(u64, u64, u64)> {
        let counters = self.try_as_ref()?.export_counters();
        Ok((
            counters.metrics_export_attempts,
            counters.metrics_export_successes,
            counters.metrics_export_failures,
        ))
    }

    fn force_flush(&self, py: Python<'_>) -> PyResult<()> {
        let aggregator = self.try_as_ref()?;
        py.detach(|| aggregator.force_flush())
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
        Ok(())
    }

    fn shutdown(&mut self, py: Python<'_>) -> PyResult<()> {
        if let Some(aggregator) = self.inner.take() {
            let result = py.detach(move || aggregator.shutdown());
            self.callbacks_enabled.store(false, Ordering::Release);
            result.map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
        } else {
            self.callbacks_enabled.store(false, Ordering::Release);
        }
        Ok(())
    }
}

impl Drop for OtelMetricsProviderPy {
    fn drop(&mut self) {
        self.callbacks_enabled.store(false, Ordering::Release);
        if let Some(aggregator) = self.inner.take() {
            std::thread::spawn(move || drop(aggregator));
        }
    }
}

#[pymodule]
pub fn register_otel_telemetry(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(build_otel_metrics_provider, m)?)?;
    m.add_class::<OtelMetricsProviderPy>()?;
    Ok(())
}
