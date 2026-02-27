use datadog_remote_config::{RemoteConfigData, RemoteConfigProduct};
use datadog_tracer_flare::{error::FlareError, FlareAction, TracerFlareManager};
use pyo3::{create_exception, exceptions::PyException, prelude::*, Bound, PyErr};

create_exception!(
    tracer_flare_exceptions,
    ListeningError,
    PyException,
    "Listening error"
);
create_exception!(
    tracer_flare_exceptions,
    LockError,
    PyException,
    "Lock error"
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
create_exception!(tracer_flare_exceptions, ZipError, PyException, "Zip error");

pub struct FlareErrorPy(pub FlareError);

impl From<FlareErrorPy> for PyErr {
    fn from(value: FlareErrorPy) -> Self {
        match value.0 {
            FlareError::ListeningError(msg) => ListeningError::new_err(msg),
            FlareError::LockError(msg) => LockError::new_err(msg),
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

fn register_exceptions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("ListeningError", m.py().get_type::<ListeningError>())?;
    m.add("LockError", m.py().get_type::<LockError>())?;
    m.add("ParsingError", m.py().get_type::<ParsingError>())?;
    m.add("SendError", m.py().get_type::<SendError>())?;
    m.add("ZipError", m.py().get_type::<ZipError>())?;
    Ok(())
}

/// Python wrapper for FlareAction
#[pyclass(name = "FlareAction")]
#[derive(Clone)]
pub struct FlareActionPy {
    inner: FlareAction,
}

#[pymethods]
impl FlareActionPy {
    fn __repr__(&self) -> String {
        match &self.inner {
            FlareAction::Send(task) => {
                format!(
                    "FlareAction.Send(case_id={}, uuid={})",
                    task.args.case_id, task.uuid
                )
            }
            FlareAction::Set(level) => format!("FlareAction.Set({level:?})"),
            FlareAction::Unset => "FlareAction.Unset".to_string(),
            FlareAction::None => "FlareAction.None".to_string(),
        }
    }

    fn is_send(&self) -> bool {
        matches!(self.inner, FlareAction::Send(_))
    }

    fn is_set(&self) -> bool {
        matches!(self.inner, FlareAction::Set(_))
    }

    fn is_unset(&self) -> bool {
        matches!(self.inner, FlareAction::Unset)
    }

    #[getter]
    fn level(&self) -> PyResult<Option<String>> {
        match &self.inner {
            FlareAction::Set(level) => Ok(Some(level.to_string())),
            _ => Ok(None),
        }
    }

    #[getter]
    fn case_id(&self) -> PyResult<Option<String>> {
        match &self.inner {
            FlareAction::Send(task) => Ok(Some(task.args.case_id.to_string())),
            _ => Ok(None),
        }
    }

    #[staticmethod]
    fn none_action() -> FlareActionPy {
        FlareActionPy {
            inner: FlareAction::None,
        }
    }
}

impl From<FlareAction> for FlareActionPy {
    fn from(value: FlareAction) -> Self {
        FlareActionPy { inner: value }
    }
}

#[pyclass(name = "TracerFlareManager")]
pub struct TracerFlareManagerPy {
    manager: TracerFlareManager,
}

#[pymethods]
impl TracerFlareManagerPy {
    /// Creates a new TracerFlareManager with basic configuration (no listener).
    ///
    /// Args:
    ///     agent_url: Agent URL computed from the environment
    ///
    /// Returns:
    ///     TracerFlareManager instance
    #[new]
    fn new(agent_url: &str) -> Self {
        TracerFlareManagerPy {
            manager: TracerFlareManager::new(agent_url, "python"),
        }
    }

    /// Handles incoming remote configuration data and determines the appropriate action.
    ///
    /// Args:
    ///     data: Raw bytes of the remote configuration payload
    ///     product: The product type of the remote configuration (e.g., "AGENT_CONFIG", "AGENT_TASK")
    ///
    /// Returns:
    ///     FlareAction indicating what action to take based on the remote configuration
    ///
    /// Raises:
    ///     ParsingError: If the product type is unexpected or if parsing the data fails
    fn handle_remote_config_data(&self, data: &[u8], product: &str) -> PyResult<FlareActionPy> {
        let manager = &self.manager;

        let product: RemoteConfigProduct = match product {
            "AGENT_CONFIG" => RemoteConfigProduct::AgentConfig,
            "AGENT_TASK" => RemoteConfigProduct::AgentTask,
            _ => {
                return Err(ParsingError::new_err(format!(
                    "Received unexpected tracer flare product type: {}",
                    product
                )));
            }
        };

        let config_data: RemoteConfigData = RemoteConfigData::try_parse(product, data)
            .map_err(|e| ParsingError::new_err(format!("Parsing error: {}", e)))?;

        Ok(manager
            .handle_remote_config_data(&config_data)
            .map_err(|e| ParsingError::new_err(format!("Parsing error for AGENT_CONFIG: {}", e)))?
            .into())
    }

    /// Zips files from a directory and sends them to the agent.
    ///
    /// Args:
    ///     directory: Path to directory containing files to include in the zip
    ///     send_action: FlareAction that must be a Send action
    ///
    /// Returns:
    ///     None
    ///
    /// Raises:
    ///     ZipError: If zipping fails or directory doesn't exist
    ///     SendError: If sending fails
    fn zip_and_send(&self, directory: &str, send_action: FlareActionPy) -> PyResult<()> {
        let manager = &self.manager;

        manager
            .zip_and_send_sync(vec![directory.to_string()], send_action.inner)
            .map_err(|e| FlareErrorPy::from(e).into())
    }

    /// Sets the current log level for the tracer flare manager.
    ///
    /// Args:
    ///    level: The log level to set (e.g., "DEBUG", "INFO", "WARN", "ERROR")
    fn set_current_log_level(&self, level: &str) -> PyResult<()> {
        self.manager
            .set_current_log_level(level)
            .map_err(|e| FlareErrorPy::from(e).into())
    }
}

/// END

#[pymodule]
pub fn native_flare(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TracerFlareManagerPy>()?;
    m.add_class::<FlareActionPy>()?;
    register_exceptions(m)?;

    Ok(())
}
