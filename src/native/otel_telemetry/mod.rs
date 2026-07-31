use libdd_otel_telemetry::{
    InstrumentDescriptor, InstrumentId, InstrumentKind, OtelMetricsAggregator,
    OtelMetricsAggregatorBuilder, OtlpExporterConfig, OtlpProtocol, ResourceBuilder, Temporality,
};
use libdd_shared_runtime::ForkSafeRuntime;
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    prelude::*,
};
use std::time::Duration;

use crate::shared_runtime::SharedRuntimePy;

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
    fn build(
        &mut self,
        shared_runtime: PyRef<'_, SharedRuntimePy>,
    ) -> PyResult<(OtelMetricsAggregatorPy, Vec<String>)> {
        let builder = self
            .try_take_builder()?
            .with_resource(std::mem::take(&mut self.resource).build());
        let runtime = shared_runtime.as_arc();
        let (aggregator, warnings) = builder.build::<ForkSafeRuntime>(runtime);
        let warnings = warnings.iter().map(|w| w.to_string()).collect();
        Ok((
            OtelMetricsAggregatorPy {
                inner: Some(aggregator),
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

    fn record_counter(&self, id: u64, value: f64, attrs: Vec<(String, String)>) -> PyResult<()> {
        self.try_as_ref()?
            .record_counter(InstrumentId(id), value, &attrs);
        Ok(())
    }

    fn record_up_down_counter(
        &self,
        id: u64,
        value: f64,
        attrs: Vec<(String, String)>,
    ) -> PyResult<()> {
        self.try_as_ref()?
            .record_up_down_counter(InstrumentId(id), value, &attrs);
        Ok(())
    }

    fn record_histogram(&self, id: u64, value: f64, attrs: Vec<(String, String)>) -> PyResult<()> {
        self.try_as_ref()?
            .record_histogram(InstrumentId(id), value, &attrs);
        Ok(())
    }

    fn observe_gauge(&self, id: u64, value: f64, attrs: Vec<(String, String)>) -> PyResult<()> {
        self.try_as_ref()?
            .observe_gauge(InstrumentId(id), value, &attrs);
        Ok(())
    }

    fn observe_counter(&self, id: u64, value: f64, attrs: Vec<(String, String)>) -> PyResult<()> {
        self.try_as_ref()?
            .observe_counter(InstrumentId(id), value, &attrs);
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

    fn force_flush(&self) -> PyResult<()> {
        self.try_as_ref()?
            .force_flush()
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        Ok(())
    }

    fn shutdown(&mut self) -> PyResult<()> {
        if let Some(aggregator) = self.inner.take() {
            aggregator
                .shutdown()
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        }
        Ok(())
    }

    fn drop(&mut self) -> PyResult<()> {
        drop(self.inner.take());
        Ok(())
    }
}

impl Drop for OtelMetricsAggregatorPy {
    fn drop(&mut self) {
        if let Some(aggregator) = self.inner.take() {
            let _ = aggregator.shutdown();
        }
    }
}

#[pymodule]
pub fn register_otel_telemetry(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<OtelMetricsAggregatorBuilderPy>()?;
    m.add_class::<OtelMetricsAggregatorPy>()?;
    Ok(())
}
