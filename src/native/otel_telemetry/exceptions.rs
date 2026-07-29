use libdd_otel_telemetry::TelemetryAggregatorError;
use pyo3::{create_exception, exceptions::PyException, prelude::*, PyErr};

create_exception!(
    otel_telemetry_exceptions,
    TelemetryAggregatorInternalError,
    PyException,
    "OTel telemetry aggregator internal error"
);

pub struct TelemetryAggregatorErrorPy(pub TelemetryAggregatorError);

impl From<TelemetryAggregatorErrorPy> for PyErr {
    fn from(value: TelemetryAggregatorErrorPy) -> Self {
        TelemetryAggregatorInternalError::new_err(value.0.to_string())
    }
}

impl From<TelemetryAggregatorError> for TelemetryAggregatorErrorPy {
    fn from(value: TelemetryAggregatorError) -> Self {
        Self(value)
    }
}

pub fn register_exceptions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add(
        "TelemetryAggregatorInternalError",
        m.py().get_type::<TelemetryAggregatorInternalError>(),
    )?;
    Ok(())
}
