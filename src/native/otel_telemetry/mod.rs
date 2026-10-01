use libdd_otel_telemetry::{
    parse_otlp_headers, AttributeArray, AttributeValue, InstrumentDescriptor, InstrumentId,
    InstrumentKind, KeyValue, ObservableCallback, ObservableMeasurement, OtelMetricsAggregator,
    OtelMetricsAggregatorBuilder, OtlpExporterConfig, OtlpProtocol, ResourceBuilder, Temporality,
};
use pyo3::{
    exceptions::{PyRuntimeError, PyTypeError, PyValueError},
    prelude::*,
    types::{PyAny, PyBool, PyFloat, PyInt, PyList, PyString, PyTuple},
};
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};

fn parse_attribute_scalar(value: &Bound<'_, PyAny>) -> PyResult<AttributeValue> {
    if value.is_instance_of::<PyBool>() {
        return value.extract().map(AttributeValue::Bool);
    }
    if value.is_instance_of::<PyInt>() {
        return value.extract().map(AttributeValue::I64);
    }
    if value.is_instance_of::<PyFloat>() {
        return value.extract().map(AttributeValue::F64);
    }
    if value.is_instance_of::<PyString>() {
        return value
            .extract::<String>()
            .map(|value| AttributeValue::String(value.into()));
    }
    Err(PyTypeError::new_err(
        "attribute values must be bool, int, float, str, or a homogeneous sequence of those types",
    ))
}

fn parse_attribute_array<'py>(
    values: impl IntoIterator<Item = Bound<'py, PyAny>>,
) -> PyResult<AttributeValue> {
    let values = values
        .into_iter()
        .map(|value| parse_attribute_scalar(&value))
        .collect::<PyResult<Vec<_>>>()?;
    let Some(first) = values.first() else {
        return Ok(AttributeValue::Array(AttributeArray::String(Vec::new())));
    };
    match first {
        AttributeValue::Bool(_) => values
            .into_iter()
            .map(|value| match value {
                AttributeValue::Bool(value) => Ok(value),
                _ => Err(PyTypeError::new_err(
                    "attribute value sequences must be homogeneous",
                )),
            })
            .collect::<PyResult<Vec<_>>>()
            .map(AttributeArray::Bool)
            .map(AttributeValue::Array),
        AttributeValue::I64(_) => values
            .into_iter()
            .map(|value| match value {
                AttributeValue::I64(value) => Ok(value),
                _ => Err(PyTypeError::new_err(
                    "attribute value sequences must be homogeneous",
                )),
            })
            .collect::<PyResult<Vec<_>>>()
            .map(AttributeArray::I64)
            .map(AttributeValue::Array),
        AttributeValue::F64(_) => values
            .into_iter()
            .map(|value| match value {
                AttributeValue::F64(value) => Ok(value),
                _ => Err(PyTypeError::new_err(
                    "attribute value sequences must be homogeneous",
                )),
            })
            .collect::<PyResult<Vec<_>>>()
            .map(AttributeArray::F64)
            .map(AttributeValue::Array),
        AttributeValue::String(_) => values
            .into_iter()
            .map(|value| match value {
                AttributeValue::String(value) => Ok(value),
                _ => Err(PyTypeError::new_err(
                    "attribute value sequences must be homogeneous",
                )),
            })
            .collect::<PyResult<Vec<_>>>()
            .map(AttributeArray::String)
            .map(AttributeValue::Array),
        AttributeValue::Array(_) => unreachable!("nested attribute arrays are rejected"),
        _ => unreachable!("all OpenTelemetry attribute variants are handled"),
    }
}

fn parse_attribute_value(value: &Bound<'_, PyAny>) -> PyResult<AttributeValue> {
    if let Ok(values) = value.cast::<PyList>() {
        return parse_attribute_array(values.iter());
    }
    if let Ok(values) = value.cast::<PyTuple>() {
        return parse_attribute_array(values.iter());
    }
    parse_attribute_scalar(value)
}

fn parse_attributes(
    py: Python<'_>,
    attributes: Vec<(String, Py<PyAny>)>,
) -> PyResult<Vec<KeyValue>> {
    attributes
        .into_iter()
        .map(|(key, value)| Ok(KeyValue::new(key, parse_attribute_value(value.bind(py))?)))
        .collect()
}

fn adapt_python_callback(
    callback: Py<PyAny>,
    callbacks_enabled: Arc<AtomicBool>,
) -> ObservableCallback {
    Arc::new(move || {
        if !callbacks_enabled.load(Ordering::Acquire) {
            return Vec::new();
        }
        Python::try_attach(|py| {
            match callback
                .call0(py)
                .and_then(|result| result.extract::<Vec<(f64, Vec<(String, Py<PyAny>)>)>>(py))
                .and_then(|measurements| {
                    measurements
                        .into_iter()
                        .map(|(value, attributes)| {
                            Ok(ObservableMeasurement::new(
                                value,
                                parse_attributes(py, attributes)?,
                            ))
                        })
                        .collect::<PyResult<Vec<_>>>()
                }) {
                Ok(measurements) => measurements,
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
    py: Python<'_>,
    service: Option<&str>,
    env: Option<&str>,
    version: Option<&str>,
    resource_attributes: Vec<(String, Py<PyAny>)>,
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
    for attribute in parse_attributes(py, resource_attributes)? {
        resource = resource.with_attribute(attribute.key, attribute.value);
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
    #[allow(clippy::too_many_arguments)]
    fn register_instrument(
        &self,
        py: Python<'_>,
        name: &str,
        kind: &str,
        unit: Option<&str>,
        description: Option<&str>,
        meter_name: &str,
        meter_version: Option<&str>,
        meter_schema_url: Option<&str>,
        meter_attributes: Vec<(String, Py<PyAny>)>,
        callback: Option<Py<PyAny>>,
    ) -> PyResult<u64> {
        let kind = parse_instrument_kind(kind)?;
        let mut descriptor = InstrumentDescriptor::new(name, kind)
            .with_scope(
                meter_name,
                meter_version.map(str::to_string),
                meter_schema_url.map(str::to_string),
            )
            .with_scope_attributes(parse_attributes(py, meter_attributes)?);
        if let Some(unit) = unit {
            descriptor = descriptor.with_unit(unit);
        }
        if let Some(description) = description {
            descriptor = descriptor.with_description(description);
        }
        let provider = self.try_as_ref()?;
        let id = if let Some(callback) = callback {
            if !callback.bind(py).is_callable() {
                return Err(PyTypeError::new_err("callback must be callable"));
            }
            let callback = adapt_python_callback(callback, Arc::clone(&self.callbacks_enabled));
            provider.register_observable_instrument(descriptor, callback)
        } else {
            provider.register_instrument(descriptor)
        };
        Ok(id.0)
    }

    fn record(
        &self,
        py: Python<'_>,
        id: u64,
        value: f64,
        attrs: Vec<(String, Py<PyAny>)>,
    ) -> PyResult<()> {
        let provider = self.try_as_ref()?;
        let attrs = parse_attributes(py, attrs)?;
        py.detach(|| provider.record(InstrumentId(id), value, &attrs));
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
