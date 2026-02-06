use datadog_remote_config::{RemoteConfigData, config::{agent_task::AgentTaskFile, agent_config::AgentConfigFile}};
use datadog_remote_config::config::{agent_task::AgentTask, agent_config::AgentConfig};
use datadog_tracer_flare::{error::FlareError, LogLevel, ReturnAction, TracerFlareManager};
use std::num::NonZero;

/// ERROR
use pyo3::{create_exception, exceptions::PyException, prelude::*, PyErr, Bound, types::PyDict, PyAny};

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
create_exception!(tracer_flare_exceptions, ZipError, PyException, "Zip error");

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
/// Python wrapper for LogLevel enum
#[pyclass(name = "LogLevel")]
#[derive(Clone, Copy)]
pub struct LogLevelPy(LogLevel);

#[pymethods]
impl LogLevelPy {
    #[classattr]
    const TRACE: LogLevelPy = LogLevelPy(LogLevel::Trace);
    #[classattr]
    const DEBUG: LogLevelPy = LogLevelPy(LogLevel::Debug);
    #[classattr]
    const INFO: LogLevelPy = LogLevelPy(LogLevel::Info);
    #[classattr]
    const WARN: LogLevelPy = LogLevelPy(LogLevel::Warn);
    #[classattr]
    const ERROR: LogLevelPy = LogLevelPy(LogLevel::Error);
    #[classattr]
    const CRITICAL: LogLevelPy = LogLevelPy(LogLevel::Critical);
    #[classattr]
    const OFF: LogLevelPy = LogLevelPy(LogLevel::Off);

    fn __repr__(&self) -> String {
        format!("{:?}", self.0)
    }

    fn __str__(&self) -> String {
        format!("{}", self.0)
    }
}

/// Internal wrapper for AgentTaskFile (not exposed to Python, only for conversion)
struct AgentTaskFileWrapper(AgentTaskFile);

impl<'py> FromPyObject<'_, 'py> for AgentTaskFileWrapper {
    type Error = PyErr;

    fn extract(ob: pyo3::Borrowed<'_, 'py, PyAny>) -> PyResult<Self> {
        let dict = ob.cast::<PyDict>()?;

        let args_ob = dict.get_item("args")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("args".to_string())
        })?;
        let args_dict = args_ob.cast::<PyDict>()?;

        let case_id_str: String = args_dict.get_item("case_id")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("case_id".to_string())
        })?.extract()?;

        // Parse case_id to NonZero<u64> as required by libdatadog v26
        // For debug patterns like "123-with-debug", extract the numeric prefix
        let numeric_part = if let Some(hyphen_pos) = case_id_str.find('-') {
            &case_id_str[..hyphen_pos]
        } else {
            &case_id_str[..]
        };

        let case_id: NonZero<u64> = numeric_part.parse::<u64>()
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Invalid case_id: {}", e)))?
            .try_into()
            .map_err(|_| PyErr::new::<pyo3::exceptions::PyValueError, _>("case_id cannot be zero"))?;

        let hostname: String = args_dict.get_item("hostname")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("hostname".to_string())
        })?.extract()?;
        let user_handle: String = args_dict.get_item("user_handle")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("user_handle".to_string())
        })?.extract()?;

        let task_type: String = dict.get_item("task_type")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("task_type".to_string())
        })?.extract()?;
        let uuid: String = dict.get_item("uuid")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("uuid".to_string())
        })?.extract()?;

        Ok(AgentTaskFileWrapper(AgentTaskFile {
            args: AgentTask {
                case_id,
                hostname,
                user_handle,
            },
            task_type,
            uuid,
        }))
    }
}

/// Internal wrapper for AgentConfigFile (not exposed to Python, only for conversion)
struct AgentConfigFileWrapper(AgentConfigFile);

impl<'py> FromPyObject<'_, 'py> for AgentConfigFileWrapper {
    type Error = PyErr;

    fn extract(ob: pyo3::Borrowed<'_, 'py, PyAny>) -> PyResult<Self> {
        let dict = ob.cast::<PyDict>()?;

        let name : String = dict.get_item("name")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("name".to_string())
        })?.extract()?;
        let config_ob = dict.get_item("config")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("config".to_string())
        })?;
        let config_dict = config_ob.cast::<PyDict>()?;
        let log_level: Option<String> = config_dict.get_item("log_level")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("log_level".to_string())
        })?.extract()?;

        Ok(AgentConfigFileWrapper(AgentConfigFile {
            name,
            config: AgentConfig {
                log_level,
            },
        }))
    }
}

/// Python wrapper for ReturnAction
#[pyclass(name = "ReturnAction")]
#[derive(Clone)]
pub struct ReturnActionPy {
    inner: ReturnAction,
}

#[pymethods]
impl ReturnActionPy {
    /// Create a ReturnAction.None variant
    #[staticmethod]
    fn none() -> Self {
        ReturnActionPy {
            inner: ReturnAction::None,
        }
    }

    /// Create a ReturnAction.Unset variant
    #[staticmethod]
    fn unset() -> Self {
        ReturnActionPy {
            inner: ReturnAction::Unset,
        }
    }


    /// Create a ReturnAction.Set variant
    #[staticmethod]
    fn set(level: &str) -> PyResult<Self> {
        let log_level = match level.to_uppercase().as_str() {
            "TRACE" => LogLevel::Trace,
            "DEBUG" => LogLevel::Debug,
            "INFO" => LogLevel::Info,
            "WARN" | "WARNING" => LogLevel::Warn,
            "ERROR" => LogLevel::Error,
            "CRITICAL" | "FATAL" => LogLevel::Critical,
            "OFF" => LogLevel::Off,
            _ => return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                format!("Invalid log level: {}. Valid levels are: TRACE, DEBUG, INFO, WARN, ERROR, CRITICAL, OFF", level)
            )),
        };

        Ok(ReturnActionPy {
            inner: ReturnAction::Set(log_level),
        })
    }

    fn __repr__(&self) -> String {
        match &self.inner {
            ReturnAction::Send(task) => {
                format!(
                    "ReturnAction.Send(case_id={}, uuid={})",
                    task.args.case_id, task.uuid
                )
            }
            ReturnAction::Set(level) => format!("ReturnAction.Set({level:?})"),
            ReturnAction::Unset => "ReturnAction.Unset".to_string(),
            ReturnAction::None => "ReturnAction.None".to_string(),
        }
    }

    fn is_send(&self) -> bool {
        matches!(self.inner, ReturnAction::Send(_))
    }

    fn is_set(&self) -> bool {
        matches!(self.inner, ReturnAction::Set(_))
    }

    fn is_unset(&self) -> bool {
        matches!(self.inner, ReturnAction::Unset)
    }

    fn is_none(&self) -> bool {
        matches!(self.inner, ReturnAction::None)
    }

    #[getter]
    fn level(&self) -> PyResult<Option<String>> {
        match &self.inner {
            ReturnAction::Set(level) => Ok(Some(level.to_string())),
            _ => Ok(None),
        }
    }

    #[getter]
    fn case_id(&self) -> PyResult<Option<String>> {
        match &self.inner {
            ReturnAction::Send(task) => Ok(Some(task.args.case_id.to_string())),
            _ => Ok(None),
        }
    }
}

impl From<ReturnAction> for ReturnActionPy {
    fn from(value: ReturnAction) -> Self {
        ReturnActionPy { inner: value }
    }
}

impl From<ReturnActionPy> for ReturnAction {
    fn from(value: ReturnActionPy) -> Self {
        value.inner
    }
}

#[pyclass(name = "TracerFlareManager")]
pub struct TracerFlareManagerPy {
    manager: std::sync::Arc<TracerFlareManager>,
}

#[pymethods]
impl TracerFlareManagerPy {
    /// Creates a new TracerFlareManager with basic configuration (no listener).
    ///
    /// Args:
    ///     agent_url: Agent URL computed from the environment
    ///     language: Language of the tracer (e.g., "python")
    ///
    /// Returns:
    ///     TracerFlareManager instance
    #[new]
    fn new(agent_url: &str, language: &str) -> Self {
        TracerFlareManagerPy {
            manager: std::sync::Arc::new(TracerFlareManager::new(agent_url, language)),
        }
    }

    // Example of a agent task data given to handle_remote_config_data
    // {
    //     "args": {
    //         "case_id": "12345",
    //         "hostname": "test-host",
    //         "user_handle": "user@example.com"
    //     },
    //     "task_type": "tracer_flare",
    //     "uuid": "unique-identifier"
    // }
    fn handle_remote_config_data(&self, data: &Bound<PyAny>, product: &str) -> PyResult<ReturnActionPy> {
        let manager = self.manager.as_ref();

        if product == "AGENT_CONFIG" {
            let agent_config: AgentConfigFileWrapper = data.extract()
                .map_err(|e| ParsingError::new_err(format!("Failed to extract AgentConfigFile: {}, data: {}", e, data)))?;

            return Ok(manager.handle_remote_config_data(&RemoteConfigData::TracerFlareConfig(agent_config.0))
                .map_err(|e| ParsingError::new_err(format!("Parsing error for AGENT_CONFIG: {}", e)))?.into());
        } else if product == "AGENT_TASK" {
            // Pre-validate case_id before extraction to handle invalid case_ids gracefully
            if let Ok(dict) = data.cast::<PyDict>() {
                if let Ok(Some(args)) = dict.get_item("args") {
                    if let Ok(args_dict) = args.cast::<PyDict>() {
                        if let Ok(Some(case_id_obj)) = args_dict.get_item("case_id") {
                            if let Ok(case_id_str) = case_id_obj.extract::<String>() {
                                // Check if case_id is invalid (empty or zero)
                                if case_id_str.is_empty() {
                                    return Ok(ReturnAction::None.into());
                                }

                                // Try to parse as u64, or extract numeric prefix for debug patterns
                                let numeric_part = if let Some(hyphen_pos) = case_id_str.find('-') {
                                    // Extract numeric part before hyphen (e.g., "123" from "123-with-debug")
                                    &case_id_str[..hyphen_pos]
                                } else {
                                    &case_id_str[..]
                                };

                                // Check if the numeric part is valid and non-zero
                                if numeric_part.parse::<u64>().ok().map_or(true, |v| v == 0) {
                                    // Invalid or zero case_id - return None action
                                    return Ok(ReturnAction::None.into());
                                }
                            }
                        }
                    }
                }
            }

            let agent_task: AgentTaskFileWrapper = data.extract()
                .map_err(|e| ParsingError::new_err(format!("Failed to extract AgentTaskFile: {}, data: {}", e, data)))?;

            return Ok(manager.handle_remote_config_data(&RemoteConfigData::TracerFlareTask(agent_task.0))
                .map_err(|e| ParsingError::new_err(format!("Parsing error for AGENT_TASK: {}", e)))?.into());
        } else {
            return Err(ParsingError::new_err(format!(
                "Received unexpected tracer flare product type: {}",
                product
            )));
        }
    }

    /// Zips files from a directory and sends them to the agent.
    ///
    /// Args:
    ///     directory: Path to directory containing files to include in the zip
    ///     send_action: ReturnAction that must be a Send action
    ///
    /// Returns:
    ///     None
    ///
    /// Raises:
    ///     ZipError: If zipping fails or directory doesn't exist
    ///     SendError: If sending fails
    fn zip_and_send(&self, directory: &str, send_action: ReturnActionPy) -> PyResult<()> {
        let rust_action: ReturnAction = send_action.inner;

        let manager_arc = self.manager.clone();

        // Create a new tokio runtime to run the async code.
        // Use current_thread runtime to avoid multi-threaded I/O driver issues.
        // Enable time for timeout support in libdatadog's HTTP operations.
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .enable_io()
            .build()
            .map_err(|e| {
                PyException::new_err(format!("Failed to create tokio runtime: {e}"))
            })?;

        rt.block_on(async move {
            let manager = manager_arc.as_ref();

            manager
                .zip_and_send(vec![directory.to_string()], rust_action)
                .await
                .map_err(|e| FlareErrorPy::from(e).into())
        })
    }


    fn __repr__(&self) -> String {
        "TracerFlareManager".to_string()
    }
}

/// END

#[pymodule]
pub fn native_flare(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TracerFlareManagerPy>()?;
    m.add_class::<ReturnActionPy>()?;
    m.add_class::<LogLevelPy>()?;
    register_exceptions(m)?;

    Ok(())
}
