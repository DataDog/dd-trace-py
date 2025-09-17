use anyhow;
use std::collections::HashMap;
use std::ffi::{c_void, CString};
use pyo3::ffi;
use std::sync::atomic::{AtomicU8, AtomicPtr, Ordering};
use std::sync::Once;
use std::time::Duration;
use std::ptr;

use datadog_crashtracker::{
    register_runtime_stack_callback, CallbackError, CrashtrackerConfiguration,
    CrashtrackerReceiverConfig, Metadata, RuntimeStackFrame, StacktraceCollection,
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


// Global storage for the Python callback - using atomic pointer for signal safety
static PYTHON_CALLBACK: AtomicPtr<PyObject> = AtomicPtr::new(ptr::null_mut());

/// Runtime-specific stack frame representation for FFI
///
/// This struct is used to pass runtime stack frame information from language
/// runtimes to the crashtracker during crash handling.
#[pyclass(name = "RuntimeStackFrame", module = "datadog.internal._native")]
#[derive(Debug, Clone)]
pub struct RuntimeStackFramePy {
    pub function_name: Option<String>,
    pub file_name: Option<String>,
    pub line_number: u32,
    pub column_number: u32,
    pub class_name: Option<String>,
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

// Thread-local storage for the current emit_frame function (signal context)
thread_local! {
    static EMIT_FRAME_FN: std::cell::Cell<Option<unsafe extern "C" fn(*const RuntimeStackFrame)>> =
        const { std::cell::Cell::new(None) };
}

// Helper function to emit a frame from Python during crash context
#[pyfunction(name = "emit_python_frame")]
pub fn emit_python_frame(frame: &RuntimeStackFramePy) -> PyResult<()> {
    eprintln!("DEBUG: emit_python_frame called with function: {:?}", frame.function_name);

    EMIT_FRAME_FN.with(|emit_fn_cell| {
        if let Some(emit_frame_fn) = emit_fn_cell.get() {
            eprintln!("DEBUG: Found emit_frame_fn, creating C frame");

            // Create CStrings that will live for the duration of this function call
            let function_c_str = frame.function_name.as_ref().and_then(|s| CString::new(s.as_str()).ok());
            let file_c_str = frame.file_name.as_ref().and_then(|s| CString::new(s.as_str()).ok());
            let class_c_str = frame.class_name.as_ref().and_then(|s| CString::new(s.as_str()).ok());
            let module_c_str = frame.module_name.as_ref().and_then(|s| CString::new(s.as_str()).ok());

            let c_frame = RuntimeStackFrame {
                function_name: function_c_str.as_ref().map_or(std::ptr::null(), |s| s.as_ptr()),
                file_name: file_c_str.as_ref().map_or(std::ptr::null(), |s| s.as_ptr()),
                line_number: frame.line_number,
                column_number: frame.column_number,
                class_name: class_c_str.as_ref().map_or(std::ptr::null(), |s| s.as_ptr()),
                module_name: module_c_str.as_ref().map_or(std::ptr::null(), |s| s.as_ptr()),
            };

            eprintln!("DEBUG: About to call emit_frame_fn");
            unsafe {
                emit_frame_fn(&c_frame);
            }
            eprintln!("DEBUG: emit_frame_fn called successfully");
        } else {
            eprintln!("DEBUG: emit_python_frame called but no emit function available");
        }
    });
    Ok(())
}

// C callback function that bridges to Python
unsafe extern "C" fn python_callback_bridge(
    emit_frame: unsafe extern "C" fn(*const RuntimeStackFrame),
    _context: *mut c_void,
) {
    eprintln!("DEBUG: Python callback bridge invoked");

    // Store the emit_frame function in thread-local storage for this callback execution
    EMIT_FRAME_FN.with(|emit_fn_cell| {
        emit_fn_cell.set(Some(emit_frame));
    });

    let callback_ptr = PYTHON_CALLBACK.load(Ordering::SeqCst);
    if !callback_ptr.is_null() {
        eprintln!("DEBUG: Found Python callback, calling it");

        // This is unsafe, but necessary for runtime callbacks
        Python::with_gil(|py| {
            let py_callback = &*callback_ptr;
            // Call the Python callback - it should call emit_python_frame for each frame
            if let Err(_e) = py_callback.call0(py) {
                eprintln!("DEBUG: Error calling Python callback");
            } else {
                eprintln!("DEBUG: Python callback executed successfully");
            }
        });
    } else {
        eprintln!("DEBUG: No Python callback found");
    }

    // Clear the emit_frame function after callback execution
    EMIT_FRAME_FN.with(|emit_fn_cell| {
        emit_fn_cell.set(None);
    });
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
    // Create a boxed PyObject on the heap so it has a stable address
    let callback_box = Box::new(callback.clone_ref(py));
    let callback_ptr = Box::into_raw(callback_box);

    // Try to store the callback pointer atomically
    let previous = PYTHON_CALLBACK.compare_exchange(
        std::ptr::null_mut(),
        callback_ptr,
        Ordering::SeqCst,
        Ordering::SeqCst,
    );

    match previous {
        Ok(_) => {
            match register_runtime_stack_callback(python_callback_bridge, std::ptr::null_mut()) {
                Ok(()) => CallbackResult::Ok,
                Err(e) => e.into(),
            }
        }
        Err(_) => {
            let _ = unsafe { Box::from_raw(callback_ptr) };
            CallbackResult::AlreadyRegistered
        }
    }
}

// Native C callback function that directly emits runtime stack frames
unsafe extern "C" fn native_runtime_stack_callback(
    emit_frame: unsafe extern "C" fn(*const RuntimeStackFrame),
    _context: *mut c_void,
) {
    Python::with_gil(|py| {
        let frame = py.eval(ffi::c_str!("__import__('sys')._getframe()"), None, None);
        if let Ok(frame_obj) = frame {
            emit_python_stack_from_frame(emit_frame, py, frame_obj);
        }
    });
}

fn emit_python_stack_from_frame(
    emit_frame: unsafe extern "C" fn(*const RuntimeStackFrame),
    py: Python,
    frame_obj: Bound<'_, PyAny>,
) {
    let code = frame_obj.getattr("f_code");
    let back = frame_obj.getattr("f_back");

    if let (Ok(code_obj), Ok(back_obj)) = (code, back) {
        let function_name = code_obj.getattr("co_name")
            .and_then(|n| n.extract::<String>())
            .unwrap_or_else(|_| "<unknown>".to_string());

        let file_name = code_obj.getattr("co_filename")
            .and_then(|f| f.extract::<String>())
            .unwrap_or_else(|_| "<unknown>".to_string());

        let line_number = frame_obj.getattr("f_lineno")
            .and_then(|l| l.extract::<u32>())
            .unwrap_or(0);

        let function_c_str = CString::new(function_name).unwrap_or_else(|_| CString::new("<invalid>").unwrap());
        let file_c_str = CString::new(file_name).unwrap_or_else(|_| CString::new("<invalid>").unwrap());

        let c_frame = RuntimeStackFrame {
            function_name: function_c_str.as_ptr(),
            file_name: file_c_str.as_ptr(),
            line_number,
            column_number: 0,
            class_name: std::ptr::null(),
            module_name: std::ptr::null(),
        };

        unsafe {
            emit_frame(&c_frame);
        }

        if !back_obj.is_none() {
            emit_python_stack_from_frame(emit_frame, py, back_obj);
        }
    }
}

/// Register the native runtime stack collection callback
///
/// This function registers a native callback that directly collects Python runtime
/// stack traces without requiring Python callback functions.
///
/// # Returns
/// - `CallbackResult::Ok` if registration succeeds
/// - `CallbackResult::AlreadyRegistered` if a callback is already registered
///
/// # Safety
/// - Only one callback can be registered at a time
#[pyfunction(name = "crashtracker_register_native_runtime_callback")]
pub fn crashtracker_register_native_runtime_callback() -> CallbackResult {
    match register_runtime_stack_callback(native_runtime_stack_callback, std::ptr::null_mut()) {
        Ok(()) => CallbackResult::Ok,
        Err(e) => e.into(),
    }
}
