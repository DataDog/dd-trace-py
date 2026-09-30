use libdd_otel_telemetry::{
    InstrumentDescriptor, InstrumentId, InstrumentKind, ObservableCallback, ObservableMeasurement,
    OtelMetricsAggregator, OtelMetricsAggregatorBuilder, OtlpExporterConfig, OtlpProtocol,
    ResourceBuilder, Temporality,
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

/// A wrapper around [OtelMetricsAggregatorBuilder].
///
/// Allows using the builder as a python class. Only one aggregator can be built using a builder;
/// once `build` has been called the builder shouldn't be reused.
#[pyclass(name = "OtelMetricsAggregatorBuilder")]
pub struct OtelMetricsAggregatorBuilderPy {
    builder: Option<OtelMetricsAggregatorBuilder>,
    resource: ResourceBuilder,
}

impl OtelMetricsAggregatorBuilderPy {
    fn try_take_builder(&mut self) -> PyResult<OtelMetricsAggregatorBuilder> {
        self.builder
            .take()
            .ok_or(PyValueError::new_err("Builder has already been consumed"))
    }
}

#[pymethods]
impl OtelMetricsAggregatorBuilderPy {
    #[new]
    fn new() -> Self {
        OtelMetricsAggregatorBuilderPy {
            builder: Some(OtelMetricsAggregatorBuilder::new()),
            resource: ResourceBuilder::new(),
        }
    }

    fn set_resource_service(mut slf: PyRefMut<'_, Self>, service: &str) -> Py<Self> {
        slf.resource = std::mem::take(&mut slf.resource).with_service(service);
        slf.into()
    }

    fn set_resource_env(mut slf: PyRefMut<'_, Self>, env: &str) -> Py<Self> {
        slf.resource = std::mem::take(&mut slf.resource).with_env(env);
        slf.into()
    }

    fn set_resource_version(mut slf: PyRefMut<'_, Self>, version: &str) -> Py<Self> {
        slf.resource = std::mem::take(&mut slf.resource).with_version(version);
        slf.into()
    }

    fn set_resource_attribute(mut slf: PyRefMut<'_, Self>, key: &str, value: &str) -> Py<Self> {
        slf.resource = std::mem::take(&mut slf.resource).with_attribute(key, value);
        slf.into()
    }

    /// `protocol` is one of `"grpc"` or `"http/protobuf"`.
    fn set_metrics_exporter(
        mut slf: PyRefMut<'_, Self>,
        endpoint: &str,
        protocol: &str,
        timeout_ms: u64,
        headers: Vec<(String, String)>,
    ) -> PyResult<Py<Self>> {
        let protocol = parse_protocol(protocol)?;
        let mut config = OtlpExporterConfig::new(endpoint, protocol)
            .with_timeout(Duration::from_millis(timeout_ms));
        for (key, value) in headers {
            config = config.with_header(key, value);
        }
        let builder = slf.try_take_builder()?;
        slf.builder = Some(builder.with_metrics_exporter(config));
        Ok(slf.into())
    }

    /// `temporality` is one of `"delta"` or `"cumulative"`.
    fn set_metrics_temporality(
        mut slf: PyRefMut<'_, Self>,
        temporality: &str,
    ) -> PyResult<Py<Self>> {
        let temporality = Temporality::from_config_str(temporality);
        let builder = slf.try_take_builder()?;
        slf.builder = Some(builder.with_metrics_temporality(temporality));
        Ok(slf.into())
    }

    fn set_export_interval(mut slf: PyRefMut<'_, Self>, interval_ms: u64) -> PyResult<Py<Self>> {
        let builder = slf.try_take_builder()?;
        slf.builder = Some(builder.with_export_interval(Duration::from_millis(interval_ms)));
        Ok(slf.into())
    }

    /// Consumes the wrapped builder. Returns the built aggregator together with any build
    /// warnings (e.g. an unsupported protocol for the compiled-in feature set) as plain strings
    /// for the caller to log — a misconfigured OTel pipeline never prevents this from succeeding.
    fn build(&mut self) -> PyResult<(OtelMetricsAggregatorPy, Vec<String>)> {
        let builder = self
            .try_take_builder()?
            .with_resource(std::mem::take(&mut self.resource));
        let (aggregator, warnings) = builder
            .build_with_default_runtime()
            .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
        let warnings = warnings.iter().map(|w| w.to_string()).collect();
        Ok((
            OtelMetricsAggregatorPy {
                inner: Some(aggregator),
                callbacks_enabled: Arc::new(AtomicBool::new(true)),
            },
            warnings,
        ))
    }

    fn debug(&self) -> String {
        format!("{:?}", self.resource)
    }
}

/// A python object wrapping a [OtelMetricsAggregator] instance.
#[pyclass(name = "OtelMetricsAggregator")]
pub struct OtelMetricsAggregatorPy {
    inner: Option<OtelMetricsAggregator>,
    callbacks_enabled: Arc<AtomicBool>,
}

impl OtelMetricsAggregatorPy {
    fn try_as_ref(&self) -> PyResult<&OtelMetricsAggregator> {
        self.inner.as_ref().ok_or(PyValueError::new_err(
            "OtelMetricsAggregator has already been shut down",
        ))
    }
}

#[pymethods]
impl OtelMetricsAggregatorPy {
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

    fn observe_counter(
        &self,
        py: Python<'_>,
        id: u64,
        value: f64,
        attrs: Vec<(String, String)>,
    ) -> PyResult<()> {
        let aggregator = self.try_as_ref()?;
        py.detach(|| aggregator.observe_counter(InstrumentId(id), value, &attrs));
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

    fn drop(&mut self, py: Python<'_>) -> PyResult<()> {
        self.callbacks_enabled.store(false, Ordering::Release);
        if let Some(aggregator) = self.inner.take() {
            py.detach(move || drop(aggregator));
        }
        Ok(())
    }
}

impl Drop for OtelMetricsAggregatorPy {
    fn drop(&mut self) {
        self.callbacks_enabled.store(false, Ordering::Release);
        if let Some(aggregator) = self.inner.take() {
            std::thread::spawn(move || drop(aggregator));
        }
    }
}

#[pymodule]
pub fn register_otel_telemetry(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<OtelMetricsAggregatorBuilderPy>()?;
    m.add_class::<OtelMetricsAggregatorPy>()?;
    Ok(())
}
