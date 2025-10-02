// CPython internal API wrapper
// This file defines Py_BUILD_CORE to access internal CPython functions
// and provides a safe C interface for Rust FFI

#define Py_BUILD_CORE 1
#include <Python.h>
#include <internal/pycore_traceback.h>

const char *crashtracker_dump_traceback_threads(int fd,
                                                 PyInterpreterState *interp,
                                                 PyThreadState *current_tstate) {
    return _Py_DumpTracebackThreads(fd, interp, current_tstate);
}

PyThreadState *crashtracker_get_current_tstate(void) {
    return PyGILState_GetThisThreadState();
}
