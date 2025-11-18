// CPython internal API wrapper

#include <Python.h>
#include <string.h>

#ifdef _WIN32
__declspec(selectany)
const char* (*_Py_DumpTracebackThreads)(int fd, PyInterpreterState* interp, PyThreadState* current_tstate) = NULL;
#else
// Use strong linking for all Python versions
extern const char*
_Py_DumpTracebackThreads(int fd, PyInterpreterState* interp, PyThreadState* current_tstate);
#endif

const char*
crashtracker_dump_traceback_threads(int fd, PyInterpreterState* interp, PyThreadState* current_tstate)
{
    // Call CPython's internal traceback dumper
    return _Py_DumpTracebackThreads(fd, interp, current_tstate);
}


PyThreadState*
crashtracker_get_current_tstate(void)
{
    return PyGILState_GetThisThreadState();
}
