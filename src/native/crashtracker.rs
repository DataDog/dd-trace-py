use anyhow;
use std::collections::HashMap;
use std::ffi::{c_char, c_void};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Mutex, Once};
use std::time::Duration;

use datadog_crashtracker::{
    register_runtime_stack_callback, CallbackError, CrashtrackerConfiguration,
    CrashtrackerReceiverConfig, Metadata, RuntimeStackCallback, RuntimeStackFrame, StacktraceCollection,
};
use ddcommon::Endpoint;
use pyo3::prelude::*;

pub trait RustWrapper {
    type Inner;
    const INNER_TYPE_NAME: &'static str;
    fn take_inner(&mut self) -> Option<Self::Inner>;
    fn take_inner_or_err(&mut self) -> anyhow::Result<Self::Inner> {
        self.take_inner()
            .ok_or_else(|| anyhow::anyhow!("Inner value of {} is None", Self::INNER_TYPE_NAME))
    }
}

// We redefine the Enum here to expose it to Python as datadog_crashtracker::StacktraceCollection
// is defined in an external crate.
#[pyclass(
    eq,
    eq_int,
    name = "StacktraceCollection",
    module = "datadog.internal._native"
)]
#[derive(Clone, PartialEq)]
pub enum StacktraceCollectionPy {
    Disabled,
    WithoutSymbols,
    EnabledWithInprocessSymbols,
    EnabledWithSymbolsInReceiver,
}

impl From<StacktraceCollectionPy> for StacktraceCollection {
    fn from(value: StacktraceCollectionPy) -> Self {
        match value {
            StacktraceCollectionPy::Disabled => StacktraceCollection::Disabled,
            StacktraceCollectionPy::WithoutSymbols => StacktraceCollection::WithoutSymbols,
            StacktraceCollectionPy::EnabledWithInprocessSymbols => {
                StacktraceCollection::EnabledWithInprocessSymbols
            }
            StacktraceCollectionPy::EnabledWithSymbolsInReceiver => {
                StacktraceCollection::EnabledWithSymbolsInReceiver
            }
        }
    }
}

#[pyclass(
    name = "CrashtrackerConfiguration",
    module = "datadog.internal._native"
)]
#[derive(Clone)]
pub struct CrashtrackerConfigurationPy {
    config: Option<CrashtrackerConfiguration>,
}

// additional_files: Vec<String>,
// create_alt_stack: bool,
// use_alt_stack: bool,
// endpoint: Option<Endpoint>,
// resolve_frames: StacktraceCollection,
// mut signals: Vec<i32>,
// timeout_ms: u32,
// unix_socket_path: Option<String>,

#[pymethods]
impl CrashtrackerConfigurationPy {
    #[new]
    #[pyo3(signature = (additional_files, create_alt_stack, use_alt_stack, timeout_ms, resolve_frames, endpoint=None, unix_socket_path=None))]
    pub fn new(
        additional_files: Vec<String>,
        create_alt_stack: bool,
        use_alt_stack: bool,
        timeout_ms: u64,
        resolve_frames: StacktraceCollectionPy,
        endpoint: Option<&str>,
        unix_socket_path: Option<String>,
    ) -> anyhow::Result<Self> {
        let resolve_frames: StacktraceCollection = resolve_frames.into();
        let endpoint = endpoint.map(Endpoint::from_slice);

        Ok(Self {
            config: Some(CrashtrackerConfiguration::new(
                additional_files,
                create_alt_stack,
                use_alt_stack,
                endpoint,
                resolve_frames,
                datadog_crashtracker::default_signals(),
                Some(Duration::from_millis(timeout_ms)),
                unix_socket_path,
                true, /* demangle_names */
            )?),
        })
    }
}

impl RustWrapper for CrashtrackerConfigurationPy {
    type Inner = CrashtrackerConfiguration;
    const INNER_TYPE_NAME: &'static str = "CrashtrackerConfiguration";

    fn take_inner(&mut self) -> Option<Self::Inner> {
        self.config.take()
    }
}

#[pyclass(
    name = "CrashtrackerReceiverConfig",
    module = "datadog.internal._native"
)]
#[derive(Clone)]
pub struct CrashtrackerReceiverConfigPy {
    config: Option<CrashtrackerReceiverConfig>,
}

#[pymethods]
impl CrashtrackerReceiverConfigPy {
    #[new]
    #[pyo3(signature = (args, env, path_to_receiver_binary, stderr_filename=None, stdout_filename=None))]
    pub fn new(
        args: Vec<String>,
        env: HashMap<String, String>,
        path_to_receiver_binary: String,
        stderr_filename: Option<String>,
        stdout_filename: Option<String>,
    ) -> anyhow::Result<Self> {
        Ok(Self {
            config: Some(CrashtrackerReceiverConfig::new(
                args,
                env.into_iter().collect(),
                path_to_receiver_binary,
                stderr_filename,
                stdout_filename,
            )?),
        })
    }
}

impl RustWrapper for CrashtrackerReceiverConfigPy {
    type Inner = CrashtrackerReceiverConfig;
    const INNER_TYPE_NAME: &'static str = "CrashtrackerReceiverConfig";

    fn take_inner(&mut self) -> Option<Self::Inner> {
        self.config.take()
    }
}

#[pyclass(name = "CrashtrackerMetadata", module = "datadog.internal._native")]
#[derive(Clone)]
pub struct CrashtrackerMetadataPy {
    metadata: Option<Metadata>,
}

#[pymethods]
impl CrashtrackerMetadataPy {
    #[new]
    pub fn new(
        library_name: String,
        library_version: String,
        family: String,
        tags: HashMap<String, String>,
    ) -> anyhow::Result<Self> {
        Ok(Self {
            metadata: Some(Metadata::new(
                library_name,
                library_version,
                family,
                tags.into_iter().map(|(k, v)| format!("{k}:{v}")).collect(),
            )),
        })
    }
}

impl RustWrapper for CrashtrackerMetadataPy {
    type Inner = Metadata;
    const INNER_TYPE_NAME: &'static str = "Metadata";

    fn take_inner(&mut self) -> Option<Self::Inner> {
        self.metadata.take()
    }
}

#[repr(u8)]
#[pyclass(
    eq,
    eq_int,
    name = "CrashtrackerStatus",
    module = "datadog.internal._native"
)]
#[derive(PartialEq)]
pub enum CrashtrackerStatus {
    NotInitialized = 0,
    Initialized = 1,
    FailedToInitialize = 2,
}

impl std::convert::TryFrom<u8> for CrashtrackerStatus {
    type Error = anyhow::Error;

    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value {
            0 => Ok(CrashtrackerStatus::NotInitialized),
            1 => Ok(CrashtrackerStatus::Initialized),
            2 => Ok(CrashtrackerStatus::FailedToInitialize),
            _ => Err(anyhow::anyhow!(
                "Invalid value for CrashtrackerStatus: {}",
                value
            )),
        }
    }
}

static CRASHTRACKER_STATUS: AtomicU8 = AtomicU8::new(CrashtrackerStatus::NotInitialized as u8);
static INIT: Once = Once::new();

#[pyfunction(name = "crashtracker_init")]
pub fn crashtracker_init<'py>(
    mut config: PyRefMut<'py, CrashtrackerConfigurationPy>,
    mut receiver_config: PyRefMut<'py, CrashtrackerReceiverConfigPy>,
    mut metadata: PyRefMut<'py, CrashtrackerMetadataPy>,
) -> anyhow::Result<()> {
    INIT.call_once(|| {
        let (config_opt, receiver_config_opt, metadata_opt) = (
            (*config).take_inner(),
            (*receiver_config).take_inner(),
            (*metadata).take_inner(),
        );

        if let (Some(config), Some(receiver_config), Some(metadata)) =
            (config_opt, receiver_config_opt, metadata_opt)
        {
            match datadog_crashtracker::init(config, receiver_config, metadata) {
                Ok(_) => CRASHTRACKER_STATUS
                    .store(CrashtrackerStatus::Initialized as u8, Ordering::SeqCst),
                Err(e) => {
                    eprintln!("Failed to initialize crashtracker: {}", e);
                    CRASHTRACKER_STATUS.store(
                        CrashtrackerStatus::FailedToInitialize as u8,
                        Ordering::SeqCst,
                    );
                }
            }
        } else {
            eprintln!("Failed to initialize crashtracker: config, receiver_config, metadata inner values are None");
            CRASHTRACKER_STATUS.store(
                CrashtrackerStatus::FailedToInitialize as u8,
                Ordering::SeqCst,
            );
        }
    });
    Ok(())
}

#[pyfunction(name = "crashtracker_on_fork")]
pub fn crashtracker_on_fork<'py>(
    mut config: PyRefMut<'py, CrashtrackerConfigurationPy>,
    mut receiver_config: PyRefMut<'py, CrashtrackerReceiverConfigPy>,
    mut metadata: PyRefMut<'py, CrashtrackerMetadataPy>,
) -> anyhow::Result<()> {
    let inner_config = (*config).take_inner_or_err()?;
    let inner_receiver_config = (*receiver_config).take_inner_or_err()?;
    let inner_metadata = (*metadata).take_inner_or_err()?;

    // Note to self: is it possible to call crashtracker_on_fork before crashtracker_init?
    // dd-trace-py seems to start crashtracker early on.
    datadog_crashtracker::on_fork(inner_config, inner_receiver_config, inner_metadata)
}

#[pyfunction(name = "crashtracker_status")]
pub fn crashtracker_status() -> anyhow::Result<CrashtrackerStatus> {
    CrashtrackerStatus::try_from(CRASHTRACKER_STATUS.load(Ordering::SeqCst))
}

// We expose the receiver_entry_point_stdin to use from Python script, _dd_crashtracker_receiver
// command. This is to avoid distributing both the _native.so library and crashtracker receiver
// executable, which increases the size of the package.
// https://setuptools-rust.readthedocs.io/en/latest/setuppy_tutorial.html#next-steps-and-final-remarks
// This way results in referring to the same _native.so file from the receiver binary and
// and Python module. Another side effect is that we no longer has to worry about platform specific
// binary names for the receiver, since Python installs the script as a command.
#[pyfunction(name = "crashtracker_receiver")]
pub fn crashtracker_receiver() -> anyhow::Result<()> {
    datadog_crashtracker::receiver_entry_point_stdin()
}

/// Result type for runtime callback operations
#[pyclass(
    eq,
    eq_int,
    name = "CallbackResult",
    module = "datadog.internal._native"
)]
#[derive(Debug, PartialEq, Eq)]
pub enum CallbackResult {
    /// Operation succeeded
    Ok,
    /// A callback is already registered
    AlreadyRegistered,
    /// Null callback function provided
    NullCallback,
    /// An unknown error occurred
    UnknownError,
}

impl From<CallbackError> for CallbackResult {
    fn from(error: CallbackError) -> Self {
        match error {
            CallbackError::AlreadyRegistered => CallbackResult::AlreadyRegistered,
            CallbackError::NullCallback => CallbackResult::NullCallback,
        }
    }
}

// Global storage for the Python callback
static PYTHON_CALLBACK: Mutex<Option<PyObject>> = Mutex::new(None);

/// Runtime-specific stack frame representation for FFI
///
/// This struct is used to pass runtime stack frame information from language
/// runtimes to the crashtracker during crash handling.
#[pyclass(name = "RuntimeStackFrame", module = "datadog.internal._native")]
#[derive(Debug, Clone)]
pub struct RuntimeStackFramePy {
    /// Function/method name
    pub function_name: Option<String>,
    /// Source file name
    pub file_name: Option<String>,
    /// Line number in source file
    pub line_number: u32,
    /// Column number in source file (0 if unknown)
    pub column_number: u32,
    /// Class name for OOP languages (nullable)
    pub class_name: Option<String>,
    /// Module/namespace name (nullable)
    pub module_name: Option<String>,
}

#[pymethods]
impl RuntimeStackFramePy {
    #[new]
    fn new(
        function_name: Option<String>,
        file_name: Option<String>,
        line_number: u32,
        column_number: u32,
        class_name: Option<String>,
        module_name: Option<String>,
    ) -> Self {
        Self {
            function_name,
            file_name,
            line_number,
            column_number,
            class_name,
            module_name,
        }
    }

    #[getter]
    fn get_function_name(&self) -> Option<String> {
        self.function_name.clone()
    }

    #[getter]
    fn get_file_name(&self) -> Option<String> {
        self.file_name.clone()
    }

    #[getter]
    fn get_line_number(&self) -> u32 {
        self.line_number
    }

    #[getter]
    fn get_column_number(&self) -> u32 {
        self.column_number
    }

    #[getter]
    fn get_class_name(&self) -> Option<String> {
        self.class_name.clone()
    }

    #[getter]
    fn get_module_name(&self) -> Option<String> {
        self.module_name.clone()
    }
}

// C callback function that bridges to Python
unsafe extern "C" fn python_callback_bridge(
    emit_frame: unsafe extern "C" fn(*const RuntimeStackFrame),
    _context: *mut c_void,
) {
    // Get the Python callback from global storage
    let callback_guard = PYTHON_CALLBACK.lock().unwrap();
    if let Some(ref py_callback) = *callback_guard {
        Python::with_gil(|py| {
            // Create an emit_frame function that Python can call
            let emit_frame_fn = |frame: &RuntimeStackFramePy| {
                // Convert Python frame to C frame
                let function_name = frame.function_name.as_ref().map(|s| s.as_ptr() as *const c_char);
                let file_name = frame.file_name.as_ref().map(|s| s.as_ptr() as *const c_char);
                let class_name = frame.class_name.as_ref().map(|s| s.as_ptr() as *const c_char);
                let module_name = frame.module_name.as_ref().map(|s| s.as_ptr() as *const c_char);

                let c_frame = RuntimeStackFrame {
                    function_name: function_name.unwrap_or(std::ptr::null()),
                    file_name: file_name.unwrap_or(std::ptr::null()),
                    line_number: frame.line_number,
                    column_number: frame.column_number,
                    class_name: class_name.unwrap_or(std::ptr::null()),
                    module_name: module_name.unwrap_or(std::ptr::null()),
                };

                emit_frame(&c_frame);
            };

            // Create a Python wrapper for emit_frame_fn
            let emit_frame_py = PyCell::new(py, emit_frame_fn);

            // Call the Python callback with the emit_frame function
            if let Err(e) = py_callback.call1(py, (emit_frame_py,)) {
                // In signal context, we can't do much with errors
                eprintln!("Error in Python runtime callback: {:?}", e);
            }
        });
    }
}

/// Register a runtime stack collection callback
///
/// This function allows language runtimes to register a callback that will be invoked
/// during crash handling to collect runtime-specific stack traces.
///
/// # Arguments
/// - `callback`: The Python callback function to invoke during crashes
///
/// # Returns
/// - `CallbackResult::Ok` if registration succeeds
/// - `CallbackResult::AlreadyRegistered` if a callback is already registered
/// - `CallbackResult::NullCallback` if the callback function is null
///
/// # Safety
/// - The callback must be signal-safe
/// - Only one callback can be registered at a time
#[pyfunction(name = "crashtracker_register_runtime_callback")]
pub fn crashtracker_register_runtime_callback(
    py: Python,
    callback: PyObject,
) -> CallbackResult {
    // Store the Python callback
    {
        let mut callback_guard = PYTHON_CALLBACK.lock().unwrap();
        *callback_guard = Some(callback);
    }

    // Register the C bridge function
    match register_runtime_stack_callback(python_callback_bridge, std::ptr::null_mut()) {
        Ok(()) => CallbackResult::Ok,
        Err(e) => e.into(),
    }
}
