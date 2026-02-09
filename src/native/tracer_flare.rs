use datadog_remote_config::{RemoteConfigData, config::{agent_task::AgentTaskFile, agent_config::AgentConfigFile}};
use datadog_remote_config::config::{agent_task::AgentTask, agent_config::AgentConfig};
use datadog_tracer_flare::{error::FlareError, LogLevel, FlareAction, TracerFlareManager};
use regex::Regex;

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

pub fn register_exceptions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("ListeningError", m.py().get_type::<ListeningError>())?;
    m.add("LockError", m.py().get_type::<LockError>())?;
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
struct AgentTaskFileWrapper {
    inner: AgentTaskFile,
}

impl<'py> FromPyObject<'_, 'py> for AgentTaskFileWrapper {
    type Error = PyErr;

    fn extract(ob: pyo3::Borrowed<'_, 'py, PyAny>) -> PyResult<Self> {
        let dict = ob.cast::<PyDict>()?;

        let args_ob = dict.get_item("args")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("args".to_string())
        })?;
        let args_dict = args_ob.cast::<PyDict>()?;

        let case_id: String = args_dict.get_item("case_id")?.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyKeyError, _>("case_id".to_string())
        })?.extract()?;

        if case_id.is_empty() || case_id == "0" {
            return Err(ParsingError::new_err(format!("Invalid case_id: '{}'", case_id)));
        }
        if !case_id.chars().all(|c| c.is_ascii_digit()) {
            let case_id_regex = Regex::new(r"^\d+-(with-debug|with-content)$").map_err(|e| ParsingError::new_err(format!("Failed to compile case_id regex: {}", e)))?;
            if !case_id_regex.is_match(&case_id) {
                return Err(ParsingError::new_err(format!("Invalid case_id format: '{}'", case_id)));
            }
        }

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

        Ok(Self {
            inner: AgentTaskFile {
                args: AgentTask {
                    case_id,
                    hostname,
                    user_handle,
                },
                task_type,
                uuid,
            },
        })
    }
}

/// Internal wrapper for AgentConfigFile (not exposed to Python, only for conversion)
struct AgentConfigFileWrapper {
    inner: AgentConfigFile,
}

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

        Ok(Self {
            inner: AgentConfigFile {
                name,
                config: AgentConfig {
                    log_level,
                },
            },
        })
    }
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
}

impl From<FlareAction> for FlareActionPy {
    fn from(value: FlareAction) -> Self {
        FlareActionPy { inner: value }
    }
}

impl From<FlareActionPy> for FlareAction {
    fn from(value: FlareActionPy) -> Self {
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

    fn handle_remote_config_data(&self, data: &Bound<PyAny>, product: &str) -> PyResult<FlareActionPy> {
        let manager = self.manager.as_ref();

        if product == "AGENT_CONFIG" {
            let agent_config: AgentConfigFileWrapper = match data.extract() {
                Ok(agent_config) => agent_config,
                Err(_) => {
                    return Ok(FlareActionPy::from(FlareAction::None));
                }
            };
            return Ok(manager.handle_remote_config_data(&RemoteConfigData::TracerFlareConfig(agent_config.inner))
                .map_err(|e| ParsingError::new_err(format!("Parsing error for AGENT_CONFIG: {}", e)))?.into());

        } else if product == "AGENT_TASK" {
            let agent_task: AgentTaskFileWrapper = match data.extract() {
                Ok(agent_task) => agent_task,
                Err(_) => {
                    return Ok(FlareActionPy::from(FlareAction::None));
                }
            };
            return Ok(manager.handle_remote_config_data(&RemoteConfigData::TracerFlareTask(agent_task.inner))
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
    ///     send_action: FlareAction that must be a Send action
    ///
    /// Returns:
    ///     None
    ///
    /// Raises:
    ///     ZipError: If zipping fails or directory doesn't exist
    ///     SendError: If sending fails
    fn zip_and_send(&self, directory: &str, send_action: FlareActionPy) -> PyResult<()> {
        let rust_action: FlareAction = send_action.inner;

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
    m.add_class::<FlareActionPy>()?;
    m.add_class::<LogLevelPy>()?;
    register_exceptions(m)?;

    Ok(())
}
