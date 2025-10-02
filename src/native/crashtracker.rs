use anyhow;
use std::collections::HashMap;
use std::ffi::{c_char, c_int, c_void};
use std::ptr;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Once;
use std::time::Duration;

use datadog_crashtracker::{
    get_registered_runtime_type_ptr, is_runtime_callback_registered,
    register_runtime_stack_callback, CallbackError, CallbackType, CrashtrackerConfiguration,
    CrashtrackerReceiverConfig, Metadata, RuntimeStackFrame, RuntimeType, StacktraceCollection,
};
use ddcommon::Endpoint;
use pyo3::prelude::*;

extern "C" {
    fn crashtracker_dump_traceback_threads(
        fd: c_int,
        interp: *mut pyo3_ffi::PyInterpreterState,
        current_tstate: *mut pyo3_ffi::PyThreadState,
    ) -> *const c_char;

    fn crashtracker_get_current_tstate() -> *mut pyo3_ffi::PyThreadState;

    fn pipe(pipefd: *mut [c_int; 2]) -> c_int;
    fn read(fd: c_int, buf: *mut c_void, count: usize) -> isize;
    fn close(fd: c_int) -> c_int;
    fn fcntl(fd: c_int, cmd: c_int, arg: c_int) -> c_int;
}

// Constants for fcntl
const F_SETFL: c_int = 4;
const O_NONBLOCK: c_int = 0o4000;

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
    type Inner = datadog_crashtracker::Metadata;
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
    Ok,
    NullCallback,
    UnknownError,
}

impl From<CallbackError> for CallbackResult {
    fn from(error: CallbackError) -> Self {
        match error {
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
const MAX_TRACEBACK_SIZE: usize = 64 * 1024; // 64KB buffer for traceback text

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

    fn as_ptr(&self) -> *const c_char {
        self.data.as_ptr() as *const c_char
    }

    fn set_from_str(&mut self, s: &str) {
        let bytes = s.as_bytes();
        let copy_len = bytes.len().min(MAX_STRING_LEN - 1);
        self.data[..copy_len].copy_from_slice(&bytes[..copy_len]);
        self.data[copy_len] = 0;
        self.len = copy_len;
    }
}

// Parse a single traceback line into frame information
// '  File "/path/to/file.py", line 42, in function_name'
fn parse_traceback_line(
    line: &str,
    function_buf: &mut StackBuffer,
    file_buf: &mut StackBuffer,
) -> u32 {
    let trimmed = line.trim();

    // Look for the pattern: File "filename", line number, in function_name
    if let Some(file_start) = trimmed.find('"') {
        if let Some(file_end) = trimmed[file_start + 1..].find('"') {
            let file_path = &trimmed[file_start + 1..file_start + 1 + file_end];
            file_buf.set_from_str(file_path);

            let after_file = &trimmed[file_start + file_end + 2..];
            if let Some(line_start) = after_file.find("line ") {
                let line_part = &after_file[line_start + 5..];

                // Try to find comma first ("line 42, in func")
                let line_num = if let Some(line_end) = line_part.find(',') {
                    let line_str = line_part[..line_end].trim();
                    line_str.parse::<u32>().unwrap_or(0)
                } else {
                    // No comma, try space ("line 42 in func")
                    if let Some(space_pos) = line_part.find(' ') {
                        let line_str = line_part[..space_pos].trim();
                        line_str.parse::<u32>().unwrap_or(0)
                    } else {
                        // Just numbers until end
                        let line_str = line_part.trim();
                        line_str.parse::<u32>().unwrap_or(0)
                    }
                };

                // Look for function name
                if let Some(in_pos) = after_file.find(" in ") {
                    let func_name = after_file[in_pos + 4..].trim();
                    function_buf.set_from_str(func_name);
                } else {
                    function_buf.set_from_str("<unknown>");
                }

                return line_num;
            }
        }
    }

    // Fallback parsing
    function_buf.set_from_str("<parse_failed>");
    file_buf.set_from_str("<parse_failed>");
    0
}

// Parse traceback text and emit frames
unsafe fn parse_and_emit_traceback(
    traceback_text: &str,
    emit_frame: unsafe extern "C" fn(*mut c_void, *const RuntimeStackFrame),
    writer_ctx: *mut c_void,
) {
    let lines: Vec<&str> = traceback_text.lines().collect();
    let mut frame_count = 0;

    for line in lines {
        if frame_count >= MAX_FRAMES {
            break;
        }

        // Look for lines that start with "  File " - these are stack frame lines
        if line.trim_start().starts_with("File ") {
            let mut function_buf = StackBuffer::new();
            let mut file_buf = StackBuffer::new();

            let line_number = parse_traceback_line(line, &mut function_buf, &mut file_buf);

            let c_frame = RuntimeStackFrame {
                function_name: function_buf.as_ptr(),
                file_name: file_buf.as_ptr(),
                line_number,
                column_number: 0,
                class_name: ptr::null(),
                module_name: ptr::null(),
            };

            emit_frame(writer_ctx, &c_frame);
            frame_count += 1;
        }
    }
}

unsafe fn dump_python_traceback_via_cpython_api(
    emit_frame: unsafe extern "C" fn(*mut c_void, *const RuntimeStackFrame),
    writer_ctx: *mut c_void,
) {
    let mut pipefd: [c_int; 2] = [0, 0];
    if pipe(&mut pipefd as *mut [c_int; 2]) != 0 {
        emit_fallback_frame(emit_frame, writer_ctx, "<pipe_creation_failed>");
        return;
    }

    let read_fd = pipefd[0];
    let write_fd = pipefd[1];

    // Make the read end non-blocking
    fcntl(read_fd, F_SETFL, O_NONBLOCK);

    // Get the current thread state safely - same approach as CPython's faulthandler
    // SIGSEGV, SIGFPE, SIGABRT, SIGBUS and SIGILL are synchronous signals and
    // are thus delivered to the thread that caused the fault.
    let current_tstate = crashtracker_get_current_tstate();

    // Call the CPython internal API via our C wrapper
    // Pass NULL for interpreter state since _Py_DumpTracebackThreads handle it internally
    let error_msg = crashtracker_dump_traceback_threads(write_fd, ptr::null_mut(), current_tstate);

    close(write_fd);

    if !error_msg.is_null() {
        close(read_fd);
        let error_str = std::ffi::CStr::from_ptr(error_msg);
        if let Ok(error_string) = error_str.to_str() {
            emit_fallback_frame(emit_frame, writer_ctx, error_string);
        } else {
            emit_fallback_frame(emit_frame, writer_ctx, "<cpython_api_error>");
        }
        return;
    }

    let mut buffer = vec![0u8; MAX_TRACEBACK_SIZE];
    let bytes_read = read(
        read_fd,
        buffer.as_mut_ptr() as *mut c_void,
        MAX_TRACEBACK_SIZE,
    );

    close(read_fd);

    if bytes_read > 0 {
        buffer.truncate(bytes_read as usize);
        if let Ok(traceback_text) = std::str::from_utf8(&buffer) {
            parse_and_emit_traceback(traceback_text, emit_frame, writer_ctx);
            return;
        }
    }

    // If we get here, something went wrong with reading the output
    emit_fallback_frame(emit_frame, writer_ctx, "<traceback_read_failed>");
}

// Helper function to emit a fallback frame with error information
unsafe fn emit_fallback_frame(
    emit_frame: unsafe extern "C" fn(*mut c_void, *const RuntimeStackFrame),
    writer_ctx: *mut c_void,
    error_msg: &str,
) {
    let mut function_buf = StackBuffer::new();
    let mut file_buf = StackBuffer::new();
    function_buf.set_from_str(error_msg);
    file_buf.set_from_str("<crashtracker_fallback>");

    let fallback_frame = RuntimeStackFrame {
        function_name: function_buf.as_ptr(),
        file_name: file_buf.as_ptr(),
        line_number: 0,
        column_number: 0,
        class_name: ptr::null(),
        module_name: ptr::null(),
    };

    emit_frame(writer_ctx, &fallback_frame);
}

/// Dump Python traceback as a complete string
///
/// This function captures the Python traceback via CPython's internal API
/// and emits it as a single string instead of parsing into individual frames.
/// This is more efficient and preserves the original Python formatting.
unsafe fn dump_python_traceback_as_string(
    emit_stacktrace_string: unsafe extern "C" fn(*mut c_void, *const c_char),
    writer_ctx: *mut c_void,
) {
    // Create a pipe to capture CPython internal traceback dump
    let mut pipefd: [c_int; 2] = [0, 0];
    if pipe(&mut pipefd as *mut [c_int; 2]) != 0 {
        emit_stacktrace_string(
            writer_ctx,
            "<pipe_creation_failed>\0".as_ptr() as *const c_char,
        );
        return;
    }

    let read_fd = pipefd[0];
    let write_fd = pipefd[1];

    // Make the read end non-blocking
    fcntl(read_fd, F_SETFL, O_NONBLOCK);

    // Get the current thread state safely - same approach as CPython's faulthandler
    // SIGSEGV, SIGFPE, SIGABRT, SIGBUS and SIGILL are synchronous signals and
    // are thus delivered to the thread that caused the fault.
    let current_tstate = crashtracker_get_current_tstate();

    // Call the CPython internal API via our C wrapper
    // Pass NULL for interpreter state - let _Py_DumpTracebackThreads handle it internally
    let error_msg = crashtracker_dump_traceback_threads(write_fd, ptr::null_mut(), current_tstate);

    close(write_fd);

    // Check for errors from _Py_DumpTracebackThreads
    if !error_msg.is_null() {
        close(read_fd);
        // Note: We can't format the error message because we're in a signal context
        // Just emit a generic error message
        emit_stacktrace_string(
            writer_ctx,
            "<cpython_api_error>\0".as_ptr() as *const c_char,
        );
        return;
    }

    // Read the traceback output
    let mut buffer = vec![0u8; MAX_TRACEBACK_SIZE];
    let bytes_read = read(
        read_fd,
        buffer.as_mut_ptr() as *mut c_void,
        MAX_TRACEBACK_SIZE,
    );

    close(read_fd);

    if bytes_read > 0 {
        buffer.truncate(bytes_read as usize);
        if let Ok(traceback_text) = std::str::from_utf8(&buffer) {
            emit_stacktrace_string(writer_ctx, traceback_text.as_ptr() as *const c_char);
            return;
        }
    }

    emit_stacktrace_string(
        writer_ctx,
        "<traceback_read_failed>\0".as_ptr() as *const c_char,
    );
}

unsafe extern "C" fn native_runtime_stack_callback(
    emit_frame: unsafe extern "C" fn(*mut c_void, *const RuntimeStackFrame),
    _emit_stacktrace_string: unsafe extern "C" fn(*mut c_void, *const c_char),
    writer_ctx: *mut c_void,
) {
    // dump_python_traceback_as_string(emit_stacktrace_string, writer_ctx);
    dump_python_traceback_via_cpython_api(emit_frame, writer_ctx);
}

/// Register the native runtime stack collection callback
///
/// This function registers a native callback that directly collects Python runtime
/// stack traces without requiring Python callback functions. It uses frame-by-frame
/// collection for detailed stack information.
///
/// # Returns
/// - `CallbackResult::Ok` if registration succeeds (replaces any existing callback)
#[pyfunction(name = "crashtracker_register_native_runtime_callback")]
pub fn crashtracker_register_native_runtime_callback() -> CallbackResult {
    match register_runtime_stack_callback(
        native_runtime_stack_callback,
        RuntimeType::Python,
        CallbackType::Frame,
    ) {
        Ok(()) => CallbackResult::Ok,
        Err(e) => e.into(),
    }
}

/// Check if a runtime callback is currently registered
///
/// # Returns
/// - `True` if a callback is registered
/// - `False` if no callback is registered
#[pyfunction(name = "crashtracker_is_runtime_callback_registered")]
pub fn crashtracker_is_runtime_callback_registered() -> bool {
    is_runtime_callback_registered()
}

/// Get the runtime type of the currently registered callback
///
/// # Returns
/// - The runtime type string if a callback is registered
/// - `None` if no callback is registered
#[pyfunction(name = "crashtracker_get_registered_runtime_type")]
pub fn crashtracker_get_registered_runtime_type() -> Option<String> {
    unsafe {
        let ptr = get_registered_runtime_type_ptr();
        if ptr.is_null() {
            None
        } else {
            let c_str = std::ffi::CStr::from_ptr(ptr);
            c_str.to_str().ok().map(|s| s.to_string())
        }
    }
}
