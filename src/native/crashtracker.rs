use anyhow;
#[cfg(unix)]
use std::cmp;
use std::collections::HashMap;
use std::ffi::{c_char, c_int, c_void};
use std::ptr;
#[cfg(unix)]
use std::slice;
#[cfg(unix)]
use std::sync::atomic::AtomicBool;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Once;
use std::time::Duration;

use libdd_common::Endpoint;
use libdd_crashtracker::{
    register_runtime_frame_callback, register_runtime_stacktrace_string_callback,
    CrashtrackerConfiguration, CrashtrackerReceiverConfig, Metadata, RuntimeStackFrame,
    StacktraceCollection,
};
use pyo3::prelude::*;

// Function pointer type for _Py_DumpTracebackThreads
type PyDumpTracebackThreadsFn = unsafe extern "C" fn(
    fd: c_int,
    interp: *mut pyo3_ffi::PyInterpreterState,
    current_tstate: *mut pyo3_ffi::PyThreadState,
) -> *const c_char;

// Cached function pointer to avoid dlsym during crash
static mut DUMP_TRACEBACK_FN: Option<PyDumpTracebackThreadsFn> = None;
static DUMP_TRACEBACK_INIT: std::sync::Once = std::sync::Once::new();

// We define these raw system calls here to be used within signal handler context.
// These direct C functions are preferred over going through Rust wrappers
extern "C" {
    fn pipe(pipefd: *mut [c_int; 2]) -> c_int;
    fn read(fd: c_int, buf: *mut c_void, count: usize) -> isize;
    fn close(fd: c_int) -> c_int;
    fn fcntl(fd: c_int, cmd: c_int, arg: c_int) -> c_int;
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

// We redefine the Enum here to expose it to Python as libdd_crashtracker::StacktraceCollection
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
                libdd_crashtracker::default_signals(),
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
    // TODO: Add this back in post Code Freeze (need to update config registry)
    // emit_runtime_stacks: bool,
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
            let should_emit_runtime_stacks = std::env::var("DD_CRASHTRACKING_EMIT_RUNTIME_STACKS")
                .ok()
                .is_some_and(|v| {
                    matches!(
                        v.to_ascii_lowercase().as_str(),
                        "true" | "yes" | "1"
                    )
                });

            if should_emit_runtime_stacks {
                unsafe {
                    init_dump_traceback_fn();
                }
                let dump_fn_available = unsafe { get_cached_dump_traceback_fn().is_some() };
                if dump_fn_available {
                    if let Err(e) =
                        register_runtime_stacktrace_string_callback(
                            native_runtime_stack_string_callback,
                        )
                    {
                        eprintln!("Failed to register runtime stacktrace callback: {}", e);
                    }
                } else if let Err(e) =
                    register_runtime_frame_callback(native_runtime_stack_frame_callback)
                {
                    eprintln!("Failed to register runtime frame callback: {}", e);
                }
            }
            match libdd_crashtracker::init(config, receiver_config, metadata) {
                Ok(_) =>
                    CRASHTRACKER_STATUS.store(CrashtrackerStatus::Initialized as u8, Ordering::SeqCst),
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
    libdd_crashtracker::on_fork(inner_config, inner_receiver_config, inner_metadata)
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
    libdd_crashtracker::receiver_entry_point_stdin()
}

const MAX_TRACEBACK_SIZE: usize = 8 * 1024; // 8KB

#[cfg(unix)]
const FRAME_FUNCTION_CAP: usize = 256;
#[cfg(unix)]
const FRAME_FILE_CAP: usize = 512;

#[cfg(unix)]
static FRAME_COLLECTION_GUARD: AtomicBool = AtomicBool::new(false);

#[cfg(unix)]
unsafe fn capture_frames_via_python(emit_frame: unsafe extern "C" fn(&RuntimeStackFrame)) {
    let current = pyo3_ffi::PyThreadState_Get();
    if !current.is_null() {
        let _ = collect_and_emit_frames_for_thread(current, emit_frame);
    }
}

#[cfg(unix)]
unsafe fn collect_and_emit_frames_for_thread(
    tstate: *mut pyo3_ffi::PyThreadState,
    emit_frame: unsafe extern "C" fn(&RuntimeStackFrame),
) -> bool {
    if tstate.is_null() {
        return false;
    }

    let mut emitted = false;
    let mut frame = thread_top_frame(tstate);

    while !frame.is_null() {
        if emit_python_frame(frame, emit_frame) {
            emitted = true;
        }
        frame = advance_frame(frame);
    }

    emitted
}

unsafe fn thread_top_frame(tstate: *mut pyo3_ffi::PyThreadState) -> *mut pyo3_ffi::PyFrameObject {
    if tstate.is_null() {
        ptr::null_mut()
    } else {
        let frame = pyo3_ffi::PyThreadState_GetFrame(tstate);
        #[cfg(not(Py_3_11))]
        {
            if !frame.is_null() {
                pyo3_ffi::Py_XINCREF(frame as *mut pyo3_ffi::PyObject);
            }
        }
        frame
    }
}

unsafe fn advance_frame(frame: *mut pyo3_ffi::PyFrameObject) -> *mut pyo3_ffi::PyFrameObject {
    if frame.is_null() {
        return ptr::null_mut();
    }
    let back = pyo3_ffi::PyFrame_GetBack(frame);
    pyo3_ffi::Py_DecRef(frame as *mut pyo3_ffi::PyObject);
    back
}

#[cfg(unix)]
unsafe fn emit_python_frame(
    frame: *mut pyo3_ffi::PyFrameObject,
    emit_frame: unsafe extern "C" fn(&RuntimeStackFrame),
) -> bool {
    if frame.is_null() {
        return false;
    }

    let file_view = get_code_attr_utf8_view(frame, b"co_filename\0");
    let function_view = get_code_attr_utf8_view(frame, b"co_name\0");
    let line_number = pyo3_ffi::PyFrame_GetLineNumber(frame);

    let file_slice = file_view
        .as_ref()
        .map(|view| view.truncated(FRAME_FILE_CAP))
        .unwrap_or(&[]);
    let function_slice = function_view
        .as_ref()
        .map(|view| view.truncated(FRAME_FUNCTION_CAP))
        .unwrap_or(&[]);

    let runtime_frame = RuntimeStackFrame {
        line: if line_number < 0 {
            0
        } else {
            line_number as u32
        },
        column: 0,
        function: function_slice,
        file: file_slice,
        type_name: &[],
    };

    emit_frame(&runtime_frame);

    if let Some(view) = file_view {
        pyo3_ffi::Py_DecRef(view.obj);
    }
    if let Some(view) = function_view {
        pyo3_ffi::Py_DecRef(view.obj);
    }
    true
}

struct Utf8View {
    ptr: *const u8,
    len: usize,
    obj: *mut pyo3_ffi::PyObject,
}

impl Utf8View {
    /// Safety: caller must ensure the underlying PyObject outlives the returned slice.
    /// This should be safe since no garbage collection should be happening while suspended in
    /// crash context
    fn truncated(&self, cap: usize) -> &[u8] {
        let len = cmp::min(self.len, cap);
        unsafe { slice::from_raw_parts(self.ptr, len) }
    }
}

/// Returns a view into a PyUnicode attribute without allocating; caller must Py_DecRef `obj`.
#[cfg(unix)]
unsafe fn get_code_attr_utf8_view(
    frame: *mut pyo3_ffi::PyFrameObject,
    attr: &[u8],
) -> Option<Utf8View> {
    let code_obj = pyo3_ffi::PyFrame_GetCode(frame) as *mut pyo3_ffi::PyObject;
    if code_obj.is_null() {
        return None;
    }
    let attr_obj = pyo3_ffi::PyObject_GetAttrString(code_obj, attr.as_ptr() as *const c_char);
    pyo3_ffi::Py_DecRef(code_obj);
    if attr_obj.is_null() {
        return None;
    }

    let mut size: pyo3_ffi::Py_ssize_t = 0;
    let data = pyo3_ffi::PyUnicode_AsUTF8AndSize(attr_obj, &mut size);
    if data.is_null() || size <= 0 {
        pyo3_ffi::Py_DecRef(attr_obj);
        return None;
    }

    Some(Utf8View {
        ptr: data as *const u8,
        len: size as usize,
        obj: attr_obj,
    })
}

// Attempt to resolve _Py_DumpTracebackThreads at runtime
// Try to link once during registration
unsafe fn init_dump_traceback_fn() {
    DUMP_TRACEBACK_INIT.call_once(|| {
        #[cfg(unix)]
        {
            extern "C" {
                fn dlsym(
                    handle: *mut std::ffi::c_void,
                    symbol: *const std::ffi::c_char,
                ) -> *mut std::ffi::c_void;
            }

            const RTLD_DEFAULT: *mut std::ffi::c_void = ptr::null_mut();

            let symbol_ptr = dlsym(
                RTLD_DEFAULT,
                b"_Py_DumpTracebackThreads\0".as_ptr() as *const std::ffi::c_char,
            );

            if !symbol_ptr.is_null() {
                DUMP_TRACEBACK_FN = Some(std::mem::transmute(symbol_ptr));
            }
        }

        #[cfg(not(unix))]
        {
            // DUMP_TRACEBACK_FN remains None on non-Unix platforms
        }
    });
}

// Get the cached function pointer; should only be called after init_dump_traceback_fn
unsafe fn get_cached_dump_traceback_fn() -> Option<PyDumpTracebackThreadsFn> {
    DUMP_TRACEBACK_FN
}

unsafe fn dump_python_traceback_as_string(
    emit_stacktrace_string: unsafe extern "C" fn(*const c_char),
) {
    // Use function linked during registration
    let dump_fn = match get_cached_dump_traceback_fn() {
        Some(func) => func,
        None => {
            emit_stacktrace_string(
                "<python_runtime_stacktrace_unavailable>\0".as_ptr() as *const c_char
            );
            return;
        }
    };

    // Create a pipe to capture CPython internal traceback dump. _Py_DumpTracebackThreads writes to
    // a fd. Reading and writing to pipe is signal-safe. We stack allocate a buffer in the beginning,
    // and use it to read the output
    let mut pipefd: [c_int; 2] = [0, 0];
    if pipe(&mut pipefd as *mut [c_int; 2]) != 0 {
        emit_stacktrace_string("<pipe_creation_failed>\0".as_ptr() as *const c_char);
        return;
    }

    let read_fd = pipefd[0];
    let write_fd = pipefd[1];

    fcntl(read_fd, libc::F_SETFL as c_int, libc::O_NONBLOCK as c_int);

    // Use null thread state for signal-safety; CPython will dump all threads.
    let error_msg = dump_fn(write_fd, ptr::null_mut(), ptr::null_mut());

    close(write_fd);

    if !error_msg.is_null() {
        close(read_fd);
        emit_stacktrace_string(error_msg as *const c_char);
        return;
    }

    let mut buffer = [0u8; MAX_TRACEBACK_SIZE];
    let bytes_read = read(
        read_fd,
        buffer.as_mut_ptr() as *mut c_void,
        MAX_TRACEBACK_SIZE,
    );

    close(read_fd);

    if bytes_read > 0 {
        let bytes_read = bytes_read as usize;
        if bytes_read < MAX_TRACEBACK_SIZE {
            buffer[bytes_read] = 0;
        } else {
            // Buffer is full; add truncation indicator
            let truncation_msg = b"\n[TRUNCATED]\0";
            let msg_len = truncation_msg.len();
            if MAX_TRACEBACK_SIZE >= msg_len {
                let start_pos = MAX_TRACEBACK_SIZE - msg_len;
                buffer[start_pos..].copy_from_slice(truncation_msg);
            }
        }
        emit_stacktrace_string(buffer.as_ptr() as *const c_char);
        return;
    }

    emit_stacktrace_string("<traceback_read_failed>\0".as_ptr() as *const c_char);
}

unsafe fn dump_python_traceback_as_frames(emit_frame: unsafe extern "C" fn(&RuntimeStackFrame)) {
    #[cfg(unix)]
    {
        if emit_frame as usize == 0 {
            return;
        }

        if FRAME_COLLECTION_GUARD
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return;
        }

        capture_frames_via_python(emit_frame);

        FRAME_COLLECTION_GUARD.store(false, Ordering::SeqCst);
    }

    #[cfg(not(unix))]
    {
        let _ = emit_frame;
    }
}

unsafe extern "C" fn native_runtime_stack_string_callback(
    emit_stacktrace_string: unsafe extern "C" fn(*const c_char),
) {
    dump_python_traceback_as_string(emit_stacktrace_string);
}

unsafe extern "C" fn native_runtime_stack_frame_callback(
    emit_frame: unsafe extern "C" fn(&RuntimeStackFrame),
) {
    dump_python_traceback_as_frames(emit_frame);
}
