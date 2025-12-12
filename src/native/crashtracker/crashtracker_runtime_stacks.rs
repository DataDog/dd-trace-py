use std::cmp;
use std::ffi::{c_char, c_int, c_void};
use std::ptr;
use std::slice;
use std::sync::Once;

use libdd_crashtracker::RuntimeStackFrame;

// We define these raw system calls here to be used within signal handler context.
// These direct C functions are preferred over going through Rust wrappers
extern "C" {
    fn pipe(pipefd: *mut [c_int; 2]) -> c_int;
    fn read(fd: c_int, buf: *mut c_void, count: usize) -> isize;
    fn close(fd: c_int) -> c_int;
    fn fcntl(fd: c_int, cmd: c_int, arg: c_int) -> c_int;
}

/************************************************************
 Emit runtime stacktrace as string using _Py_DumpTracebackThreads
************************************************************/

// Function pointer type for _Py_DumpTracebackThreads
type PyDumpTracebackThreadsFn = unsafe extern "C" fn(
    fd: c_int,
    interp: *mut pyo3_ffi::PyInterpreterState,
    current_tstate: *mut pyo3_ffi::PyThreadState,
) -> *const c_char;

// Cached function pointer to avoid dlsym during crash
static mut DUMP_TRACEBACK_FN: Option<PyDumpTracebackThreadsFn> = None;
static DUMP_TRACEBACK_INIT: Once = Once::new();

const MAX_TRACEBACK_SIZE: usize = 8 * 1024; // 8KB

// Attempt to resolve _Py_DumpTracebackThreads at runtime
// Try to link once during registration
pub unsafe fn init_dump_traceback_fn() {
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
pub unsafe fn get_cached_dump_traceback_fn() -> Option<PyDumpTracebackThreadsFn> {
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

pub unsafe extern "C" fn native_runtime_stack_string_callback(
    emit_stacktrace_string: unsafe extern "C" fn(*const c_char),
) {
    dump_python_traceback_as_string(emit_stacktrace_string);
}

/************************************************************
 Emit runtime stacktrace as frames using by traversing the frame object chain
************************************************************/

#[cfg(unix)]
const FRAME_FUNCTION_CAP: usize = 256;
#[cfg(unix)]
const FRAME_FILE_CAP: usize = 512;

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
        // We won't support Python 3.9
        #[cfg(not(Py_3_10))]
        let frame: *mut pyo3_ffi::PyFrameObject = ptr::null_mut();

        #[cfg(Py_3_10)]
        let frame = pyo3_ffi::PyThreadState_GetFrame(tstate);

        #[cfg(not(Py_3_11))]
        {
            if !frame.is_null() {
                // On CPython <= 3.10 it returns/holds a borrowed frame, so we need to
                // Py_XINCREF to keep it alive while we traverse.
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

    // For versions < 3.11, we should use `co_name`
    #[cfg(not(Py_3_11))]
    let function_view = get_code_attr_utf8_view(frame, b"co_name\0");

    // For verions 3.11+, we should use qualified name
    #[cfg(Py_3_11)]
    let function_view = get_code_attr_utf8_view(frame, b"co_qualname\0");

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

unsafe fn dump_python_traceback_as_frames(emit_frame: unsafe extern "C" fn(&RuntimeStackFrame)) {
    #[cfg(unix)]
    {
        if emit_frame as usize == 0 {
            return;
        }

        capture_frames_via_python(emit_frame);
    }

    #[cfg(not(unix))]
    {
        let _ = emit_frame;
    }
}

pub unsafe extern "C" fn native_runtime_stack_frame_callback(
    emit_frame: unsafe extern "C" fn(&RuntimeStackFrame),
) {
    dump_python_traceback_as_frames(emit_frame);
}
