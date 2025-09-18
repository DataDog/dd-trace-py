use anyhow;
use std::collections::HashMap;
use std::ffi::{c_void, c_char, c_int, c_long, CStr};
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Once;
use std::time::Duration;
use std::ptr;

use datadog_crashtracker::{
    register_runtime_stack_callback, CallbackError, CrashtrackerConfiguration,
    CrashtrackerReceiverConfig, Metadata, RuntimeStackFrame, StacktraceCollection,
};
use ddcommon::Endpoint;
use pyo3::prelude::*;

// Python C API structures for direct stack walking
// These are opaque pointers to avoid ABI dependencies
#[repr(C)]
struct PyObject {
    _private: [u8; 0],
}

#[repr(C)]
struct PyCodeObject {
    _private: [u8; 0],
}

#[repr(C)]
struct PyFrameObject {
    _private: [u8; 0],
}

#[repr(C)]
struct PyThreadState {
    _private: [u8; 0],
}

#[repr(C)]
struct PyInterpreterState {
    _private: [u8; 0],
}

// External C API functions we'll link to
// Using more stable public API functions that are available across Python versions
extern "C" {
    // Minimal set of stable Python C API functions available across versions

    // Frame access - most stable approach
    fn PyEval_GetFrame() -> *mut PyFrameObject;
    fn PyFrame_GetBack(frame: *mut PyFrameObject) -> *mut PyFrameObject;
    fn PyFrame_GetCode(frame: *mut PyFrameObject) -> *mut PyCodeObject;
    fn PyFrame_GetLineNumber(frame: *mut PyFrameObject) -> c_int;

    // Object attribute access - very stable API
    fn PyObject_GetAttrString(obj: *mut PyObject, attr_name: *const c_char) -> *mut PyObject;
    fn PyUnicode_AsUTF8(unicode: *mut PyObject) -> *const c_char;

}

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

// Constants for signal-safe operation
const MAX_FRAMES: usize = 64;
const MAX_STRING_LEN: usize = 256;

// Stack-allocated buffer for signal-safe string handling
struct StackBuffer {
    data: [u8; MAX_STRING_LEN],
    len: usize,
}

impl StackBuffer {
    const fn new() -> Self {
        Self {
            data: [0u8; MAX_STRING_LEN],
            len: 0,
        }
    }

    fn as_ptr(&self) -> *const i8 {
        self.data.as_ptr() as *const i8
    }

    fn set_from_str(&mut self, s: &str) {
        let bytes = s.as_bytes();
        let copy_len = bytes.len().min(MAX_STRING_LEN - 1);
        self.data[..copy_len].copy_from_slice(&bytes[..copy_len]);
        self.data[copy_len] = 0; // null terminator
        self.len = copy_len;
    }

    fn set_from_cstr(&mut self, cstr_ptr: *const c_char) {
        if cstr_ptr.is_null() {
            self.set_from_str("<null>");
            return;
        }

        unsafe {
            let cstr = match CStr::from_ptr(cstr_ptr).to_str() {
                Ok(s) => s,
                Err(_) => "<invalid_utf8>",
            };
            self.set_from_str(cstr);
        }
    }
}


// Static string constants for attribute names (must be null-terminated)
static CO_NAME: &[u8] = b"co_name\0";
static CO_FILENAME: &[u8] = b"co_filename\0";

unsafe fn extract_frame_info(
    frame: *mut PyFrameObject,
    function_buf: &mut StackBuffer,
    file_buf: &mut StackBuffer,
) -> u32 {
    if frame.is_null() {
        function_buf.set_from_str("<null_frame>");
        file_buf.set_from_str("<null_frame>");
        return 0;
    }

    // Get code object
    let code = PyFrame_GetCode(frame);
    if code.is_null() {
        function_buf.set_from_str("<no_code>");
        file_buf.set_from_str("<no_code>");
        return 0;
    }

    // Extract function name
    let name_obj = PyObject_GetAttrString(code as *mut PyObject, CO_NAME.as_ptr() as *const c_char);
    if !name_obj.is_null() {
        let name_cstr = PyUnicode_AsUTF8(name_obj);
        if !name_cstr.is_null() {
            function_buf.set_from_cstr(name_cstr);
        } else {
            function_buf.set_from_str("<invalid_name>");
        }
    } else {
        function_buf.set_from_str("<unknown_function>");
    }

    // Extract filename
    let filename_obj = PyObject_GetAttrString(code as *mut PyObject, CO_FILENAME.as_ptr() as *const c_char);
    if !filename_obj.is_null() {
        let filename_cstr = PyUnicode_AsUTF8(filename_obj);
        if !filename_cstr.is_null() {
            file_buf.set_from_cstr(filename_cstr);
        } else {
            file_buf.set_from_str("<invalid_filename>");
        }
    } else {
        file_buf.set_from_str("<unknown_file>");
    }

    // Get line number
    let line_num = PyFrame_GetLineNumber(frame);
    if line_num > 0 {
        line_num as u32
    } else {
        0
    }
}

unsafe fn walk_frame_chain(
    emit_frame: unsafe extern "C" fn(*const RuntimeStackFrame),
    mut frame: *mut PyFrameObject,
    depth: usize,
) {
    if depth >= MAX_FRAMES || frame.is_null() {
        return;
    }

    // Stack-allocate buffers for this frame
    let mut function_buf = StackBuffer::new();
    let mut file_buf = StackBuffer::new();

    let line_number = extract_frame_info(frame, &mut function_buf, &mut file_buf);

    let c_frame = RuntimeStackFrame {
        function_name: function_buf.as_ptr(),
        file_name: file_buf.as_ptr(),
        line_number,
        column_number: 0,
        class_name: ptr::null(),
        module_name: ptr::null(),
    };

    emit_frame(&c_frame);

    // Get the previous frame in the chain
    let back_frame = PyFrame_GetBack(frame);
    if !back_frame.is_null() {
        walk_frame_chain(emit_frame, back_frame, depth + 1);
    }
}

unsafe fn walk_current_thread_stack(
    emit_frame: unsafe extern "C" fn(*const RuntimeStackFrame),
) {
    let frame = PyEval_GetFrame();
    if !frame.is_null() {
        walk_frame_chain(emit_frame, frame, 0);
    }
}

// Native signal-safe Python frame walker
// This implementation uses direct Python C API calls
unsafe extern "C" fn native_runtime_stack_callback(
    emit_frame: unsafe extern "C" fn(*const RuntimeStackFrame),
    _context: *mut c_void,
) {
    // Walk the current thread's stack
    walk_current_thread_stack(emit_frame);
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
