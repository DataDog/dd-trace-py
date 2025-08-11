use data_pipeline::trace_exporter::error::TraceExporterError;
use pyo3::{create_exception, exceptions::PyException, prelude::*, PyErr};

create_exception!(
    trace_exporter_exceptions,
    AgentError,
    PyException,
    "Agent error"
);
create_exception!(
    trace_exporter_exceptions,
    BuilderError,
    PyException,
    "Builder error"
);
create_exception!(
    trace_exporter_exceptions,
    InternalError,
    PyException,
    "Internal error"
);
create_exception!(
    trace_exporter_exceptions,
    DeserializationError,
    PyException,
    "Deserialization error"
);
create_exception!(trace_exporter_exceptions, IoError, PyException, "Io error");
create_exception!(
    trace_exporter_exceptions,
    NetworkError,
    PyException,
    "Network error"
);
create_exception!(
    trace_exporter_exceptions,
    TimeoutError,
    PyException,
    "Timeout error"
);
create_exception!(
    trace_exporter_exceptions,
    RequestError,
    PyException,
    "Request error"
);
create_exception!(
    trace_exporter_exceptions,
    SerializationError,
    PyException,
    "Serialization error"
);

pub struct TraceExporterErrorPy(pub TraceExporterError);

impl From<TraceExporterErrorPy> for PyErr {
    fn from(value: TraceExporterErrorPy) -> Self {
        match value.0 {
            TraceExporterError::Agent(error) => AgentError::new_err(error.to_string()),
            TraceExporterError::Builder(error) => BuilderError::new_err(error.to_string()),
            TraceExporterError::Internal(error) => InternalError::new_err(error.to_string()),
            TraceExporterError::Deserialization(error) => {
                DeserializationError::new_err(error.to_string())
            }
            TraceExporterError::Io(error) => IoError::new_err(error.to_string()),
            TraceExporterError::Network(error) => NetworkError::new_err(error.to_string()),
            // PyO3 doesn't properly support adding extra fields to an exception type
            // see https://github.com/PyO3/pyo3/issues/295#issuecomment-2387253743.
            // We manually create the error message here to make sure it can be parsed in the
            // NativeWriter to access the error code.
            TraceExporterError::Request(error) => RequestError::new_err(format!(
                "Error code: {}, Response: {}",
                error.status(),
                error.msg()
            )),
            TraceExporterError::Serialization(error) => {
                SerializationError::new_err(error.to_string())
            }
        }
    }
}

impl From<TraceExporterError> for TraceExporterErrorPy {
    fn from(value: TraceExporterError) -> Self {
        Self(value)
    }
}

pub fn register_exceptions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("AgentError", m.py().get_type::<AgentError>())?;
    m.add("BuilderError", m.py().get_type::<BuilderError>())?;
    m.add("InternalError", m.py().get_type::<InternalError>())?;
    m.add(
        "DeserializationError",
        m.py().get_type::<DeserializationError>(),
    )?;
    m.add("IoError", m.py().get_type::<IoError>())?;
    m.add("NetworkError", m.py().get_type::<NetworkError>())?;
    m.add("RequestError", m.py().get_type::<RequestError>())?;
    m.add(
        "SerializationError",
        m.py().get_type::<SerializationError>(),
    )?;
    Ok(())
}
