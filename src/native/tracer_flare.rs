use datadog_remote_config::config::agent_task::AgentTaskFile;
use datadog_tracer_flare::{error::FlareError, LogLevel, ReturnAction, TracerFlareManager};

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

/// Python wrapper for AgentTaskFile
#[pyclass(name = "AgentTaskFile")]
#[derive(Clone)]
pub struct AgentTaskFilePy {
    #[pyo3(get)]
    pub case_id: u64,
    #[pyo3(get)]
    pub hostname: String,
    #[pyo3(get)]
    pub user_handle: String,
    #[pyo3(get)]
    pub task_type: String,
    #[pyo3(get)]
    pub uuid: String,
}

impl From<AgentTaskFile> for AgentTaskFilePy {
    fn from(value: AgentTaskFile) -> Self {
        AgentTaskFilePy {
            case_id: value.args.case_id.get(),
            hostname: value.args.hostname,
            user_handle: value.args.user_handle,
            task_type: value.task_type,
            uuid: value.uuid,
        }
    }
}

impl From<AgentTaskFilePy> for AgentTaskFile {
    fn from(value: AgentTaskFilePy) -> Self {
        AgentTaskFile {
            args: datadog_remote_config::config::agent_task::AgentTask {
                case_id: std::num::NonZeroU64::new(value.case_id).expect("case_id cannot be zero"),
                hostname: value.hostname,
                user_handle: value.user_handle,
            },
            task_type: value.task_type,
            uuid: value.uuid,
        }
    }
}

#[pymethods]
impl AgentTaskFilePy {
    /// Creates a new AgentTaskFile from Python.
    ///
    /// Args:
    ///     case_id: Case ID (must be non-zero)
    ///     hostname: Hostname
    ///     user_handle: User email/handle
    ///     task_type: Task type (usually "tracer_flare")
    ///     uuid: UUID for the task
    ///
    /// Returns:
    ///     AgentTaskFile instance
    #[new]
    fn new(
        case_id: u64,
        hostname: String,
        user_handle: String,
        task_type: String,
        uuid: String,
    ) -> Self {
        AgentTaskFilePy {
            case_id,
            hostname,
            user_handle,
            task_type,
            uuid,
        }
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
    /// Creates a Send action from an AgentTaskFile.
    ///
    /// Args:
    ///     task: AgentTaskFile to send
    ///
    /// Returns:
    ///     ReturnAction.Send
    #[staticmethod]
    fn send(task: AgentTaskFilePy) -> Self {
        ReturnActionPy {
            inner: ReturnAction::Send(task.into()),
        }
    }

    /// Creates a Set action with a log level.
    ///
    /// Args:
    ///     level: LogLevel to set
    ///
    /// Returns:
    ///     ReturnAction.Set
    #[staticmethod]
    fn set(level: LogLevelPy) -> Self {
        ReturnActionPy {
            inner: ReturnAction::Set(level.0),
        }
    }

    /// Creates an Unset action.
    ///
    /// Returns:
    ///     ReturnAction.Unset
    #[staticmethod]
    fn unset() -> Self {
        ReturnActionPy {
            inner: ReturnAction::Unset,
        }
    }

    /// Creates a None action.
    ///
    /// Returns:
    ///     ReturnAction.None
    #[staticmethod]
    fn none() -> Self {
        ReturnActionPy {
            inner: ReturnAction::None,
        }
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
    fn task(&self) -> PyResult<Option<AgentTaskFilePy>> {
        match &self.inner {
            ReturnAction::Send(task) => Ok(Some(task.clone().into())),
            _ => Ok(None),
        }
    }

    #[getter]
    fn level(&self) -> PyResult<Option<LogLevelPy>> {
        match &self.inner {
            ReturnAction::Set(level) => Ok(Some(LogLevelPy(*level))),
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
    manager: std::sync::Arc<std::sync::Mutex<Option<TracerFlareManager>>>,
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
            manager: std::sync::Arc::new(std::sync::Mutex::new(Some(TracerFlareManager::new(
                agent_url, language,
            )))),
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
    fn zip_and_send(&self, directory: &str, send_action: Py<PyAny>) -> PyResult<()> {
        use std::fs;
        use std::path::Path;

        // Collect all files from the directory
        let dir_path = Path::new(directory);
        if !dir_path.exists() {
            return Err(ZipError::new_err(format!(
                "Directory does not exist: {}",
                directory
            )));
        }
        if !dir_path.is_dir() {
            return Err(ZipError::new_err(format!(
                "Path is not a directory: {}",
                directory
            )));
        }

        let files: Vec<String> = fs::read_dir(dir_path)
            .map_err(|e| {
                ZipError::new_err(format!("Failed to read directory {}: {}", directory, e))
            })?
            .filter_map(|entry| {
                entry.ok().and_then(|e| {
                    let path = e.path();
                    if path.is_file() {
                        path.to_str().map(|s| s.to_string())
                    } else {
                        None
                    }
                })
            })
            .collect();

        if files.is_empty() {
            return Err(ZipError::new_err(format!(
                "No files found in directory: {}",
                directory
            )));
        }

        Python::with_gil(|py| {
            let send_action_obj = send_action.extract::<ReturnActionPy>(py)?;
            let rust_action: ReturnAction = send_action_obj.into();

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

            #[allow(clippy::await_holding_lock)]
            rt.block_on(async move {
                let manager_guard = manager_arc.lock().map_err(|e| {
                    PyException::new_err(format!("Failed to acquire manager lock: {e}"))
                })?;
                let manager = manager_guard
                    .as_ref()
                    .ok_or_else(|| PyException::new_err("Manager not initialized"))?;

                manager
                    .zip_and_send(files, rust_action)
                    .await
                    .map_err(|e| FlareErrorPy::from(e).into())
            })
        })
    }

    /// Cleans up a directory and all its contents.
    ///
    /// Args:
    ///     directory: Path to the directory to remove
    ///
    /// Returns:
    ///     None
    ///
    /// Raises:
    ///     ZipError: If cleanup fails
    fn cleanup_directory(&self, directory: &str) -> PyResult<()> {
        use std::fs;
        use std::path::Path;

        let path = Path::new(directory);
        if path.exists() {
            fs::remove_dir_all(path)
                .map_err(|e| ZipError::new_err(format!("Failed to clean up directory: {e}")))?;
        }
        Ok(())
    }

    fn __repr__(&self) -> String {
        "TracerFlareManager".to_string()
    }
}

/// END

#[pymodule]
pub fn register_tracer_flare(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<TracerFlareManagerPy>()?;
    m.add_class::<ReturnActionPy>()?;
    m.add_class::<LogLevelPy>()?;
    m.add_class::<AgentTaskFilePy>()?;
    register_exceptions(m)?;

    Ok(())
}
