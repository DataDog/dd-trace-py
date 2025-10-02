// CPython internal API wrapper header
// This provides C function declarations for accessing CPython internal APIs

#ifndef CPYTHON_INTERNAL_H
#define CPYTHON_INTERNAL_H

#include <Python.h>

#ifdef __cplusplus
extern "C" {
#endif

// Wrapper function to call _Py_DumpTracebackThreads
// Returns error message on failure, NULL on success
const char *crashtracker_dump_traceback_threads(int fd,
                                                 PyInterpreterState *interp,
                                                 PyThreadState *current_tstate);

// Wrapper to get the current thread state safely during crashes
PyThreadState *crashtracker_get_current_tstate(void);

#ifdef __cplusplus
}
#endif

#endif // CPYTHON_INTERNAL_H
