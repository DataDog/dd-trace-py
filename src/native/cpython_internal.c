// CPython internal API wrapper
#include <Python.h>
#define Py_BUILD_CORE
#include <string.h>

/*
    Optional internal CPython function.
    On Python 3.13+, this symbol may not be exported from shared libraries.
    Using weak linking so module loads even if symbol isn't available.
*/
#ifdef _WIN32
__declspec(selectany)
const char* (*_Py_DumpTracebackThreads)(int fd, PyInterpreterState* interp, PyThreadState* current_tstate) = NULL;
#else
extern const char*
_Py_DumpTracebackThreads(int fd, PyInterpreterState* interp, PyThreadState* current_tstate) __attribute__((weak));
#endif

const char*
crashtracker_dump_traceback_threads(int fd, PyInterpreterState* interp, PyThreadState* current_tstate)
{
    // Check if the internal symbol is available
#ifdef _WIN32
    if (_Py_DumpTracebackThreads != NULL) {
        return _Py_DumpTracebackThreads(fd, interp, current_tstate);
    }
#else
    if (_Py_DumpTracebackThreads != NULL) {
        return _Py_DumpTracebackThreads(fd, interp, current_tstate);
    }
#endif

    // Symbol not available (Python 3.13+ shared library) - return error for now
    // TODO: Implement faulthandler fallback
    return "Internal symbol not available - faulthandler fallback not implemented yet";
}

PyThreadState*
crashtracker_get_current_tstate(void)
{
    return PyGILState_GetThisThreadState();
}
