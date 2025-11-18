// CPython internal API wrapper

#include <Python.h>
#include <string.h>
#include <unistd.h>

// Platform-specific dynamic loading for fallback
#ifndef _WIN32
#include <dlfcn.h>
#endif

// Direct declaration of _Py_DumpTracebackThreads for static linking
// Uses weak symbol so it's NULL if cant link
extern const char*
_Py_DumpTracebackThreads(int fd, PyInterpreterState* interp, PyThreadState* current_tstate) __attribute__((weak));

const char*
crashtracker_dump_traceback_threads(int fd, PyInterpreterState* interp, PyThreadState* current_tstate)
{
    if (_Py_DumpTracebackThreads) {
        return _Py_DumpTracebackThreads(fd, interp, current_tstate);
    }

#ifndef _WIN32
    // Try dynamic linking
    static const char* (*_Py_DumpTracebackThreads_ptr)(int, PyInterpreterState*, PyThreadState*) = NULL;
    static int symbol_resolved = 0;

    if (!symbol_resolved) {
        _Py_DumpTracebackThreads_ptr =
          (const char* (*)(int, PyInterpreterState*, PyThreadState*))dlsym(RTLD_DEFAULT, "_Py_DumpTracebackThreads");
        symbol_resolved = 1;
    }

    if (_Py_DumpTracebackThreads_ptr) {
        return _Py_DumpTracebackThreads_ptr(fd, interp, current_tstate);
    }
#endif
    PyObject* faulthandler_module = PyImport_ImportModule("faulthandler");
    if (faulthandler_module == NULL) {
        PyErr_Clear();
        return NULL;
    }

    PyObject* dump_func = PyObject_GetAttrString(faulthandler_module, "dump_traceback");
    Py_DECREF(faulthandler_module);

    if (dump_func == NULL) {
        PyErr_Clear();
        return NULL;
    }

    // Call faulthandler.dump_traceback(file=fd, all_threads=True)
    PyObject* fd_obj = PyLong_FromLong(fd);
    PyObject* all_threads = Py_True;
    Py_INCREF(all_threads);

    PyObject* result = PyObject_CallFunction(dump_func, "OO", fd_obj, all_threads);

    Py_DECREF(dump_func);
    Py_DECREF(fd_obj);
    Py_DECREF(all_threads);

    if (result == NULL) {
        PyErr_Clear();
        return NULL;
    }
    Py_DECREF(result);
    return NULL; // Success
}

PyThreadState*
crashtracker_get_current_tstate(void)
{
    return PyGILState_GetThisThreadState();
}
