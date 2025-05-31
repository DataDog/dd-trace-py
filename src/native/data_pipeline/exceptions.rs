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
    RequestError,
    PyException,
    "Request error"
);
create_exception!(
    trace_exporter_exceptions,
    EncodingNotSupportedError,
    PyException,
    "Encoding not supported"
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
            TraceExporterError::Deserialization(error) => {
                DeserializationError::new_err(error.to_string())
            }
            TraceExporterError::Io(error) => IoError::new_err(error.to_string()),
            TraceExporterError::Network(error) => NetworkError::new_err(error.to_string()),
            TraceExporterError::Request(error) => {
                if error.status().as_u16() == 404 || error.status().as_u16() == 415 {
                    EncodingNotSupportedError::new_err(error.to_string())
                } else {
                    RequestError::new_err(error.to_string())
                }
            }
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
