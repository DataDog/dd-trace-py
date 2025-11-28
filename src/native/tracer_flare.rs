use datadog_tracer_flare::{
    error::FlareError,
    LogLevel, ReturnAction, TracerFlareManager,
};

/// ERROR

use pyo3::{create_exception, exceptions::PyException, prelude::*, PyErr};

create_exception!(
    tracer_flare_exceptions,
    ListeningError,
    PyException,
    "Listening error"
);
create_exception!(
    tracer_flare_exceptions,
    ParsingError,
    PyException,
    "Parsing error"
);
create_exception!(
    tracer_flare_exceptions,
    SendError,
    PyException,
    "Send error"
);
create_exception!(
    tracer_flare_exceptions,
    ZipError,
    PyException,
    "Zip error"
);

pub struct FlareErrorPy(pub FlareError);

impl From<FlareErrorPy> for PyErr {
    fn from(value: FlareErrorPy) -> Self {
        match value.0 {
            FlareError::ListeningError(msg) => ListeningError::new_err(msg),
            FlareError::ParsingError(msg) => ParsingError::new_err(msg),
            FlareError::SendError(msg) => SendError::new_err(msg),
            FlareError::ZipError(msg) => ZipError::new_err(msg),
        }
    }
}

impl From<FlareError> for FlareErrorPy {
    fn from(value: FlareError) -> Self {
        Self(value)
    }
}

pub fn register_exceptions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("ListeningError", m.py().get_type::<ListeningError>())?;
    m.add("ParsingError", m.py().get_type::<ParsingError>())?;
    m.add("SendError", m.py().get_type::<SendError>())?;
    m.add("ZipError", m.py().get_type::<ZipError>())?;
    Ok(())
}

/// LIB

// Add enum ReturnActionPy from ReturnAction

#[pyclass(name = "TracerFlareManager")]
pub struct TracerFlareManagerPy {
    manager: Option<TracerFlareManager>,
}

#[pymethods]
impl TracerFlareManagerPy {
    #[new]
    fn new(agent_url: &'_ str, language: &'_ str) -> Self {
        TracerFlareManagerPy {
            manager: Some(TracerFlareManager::new(agent_url, language)),
        }
    }

    fn handle_remote_config(&self, data: &'_ str) -> PyResult<Py<ReturnActionPy>> {
        // Use serde parsing to go from str to RemoteConfigData
        // then call handle_remote_config_data
        todo!()
    }

    /// ZIP

    // Add zip_and_send
}

/// END

#[pymodule]
pub fn register_tracer_flare(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TracerFlareManagerPy>()?;
    // Add ReturnActionPy
    register_exceptions(m)?;

    Ok(())
}
