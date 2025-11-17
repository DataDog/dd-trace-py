// CPython internal API wrapper
// This file provides a safe C interface for Rust FFI that works with both
// full Python builds (with internal symbols) and minimal builds (manylinux)
// This is because CI uses manylinux builds, but we need to access the internal
// CPython APIs

#include <Python.h>

// Platform-specific dynamic loading
#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

// Function pointer for _Py_DumpTracebackThreads (may not be available in manylinux builds)
static const char* (*_Py_DumpTracebackThreads_ptr)(int, PyInterpreterState*, PyThreadState*) = NULL;
static int symbol_resolved = 0;

// Try to resolve the symbol dynamically
static void
resolve_dump_traceback_symbol(void)
{
    if (symbol_resolved)
        return;

#ifdef _WIN32
    HMODULE hModule = GetModuleHandle(NULL); // Current process
    if (hModule) {
        _Py_DumpTracebackThreads_ptr = (const char* (*)(int, PyInterpreterState*, PyThreadState*))GetProcAddress(
          hModule, "_Py_DumpTracebackThreads");
    }
#else
    _Py_DumpTracebackThreads_ptr =
      (const char* (*)(int, PyInterpreterState*, PyThreadState*))dlsym(RTLD_DEFAULT, "_Py_DumpTracebackThreads");
#endif

    symbol_resolved = 1;
}

const char*
crashtracker_dump_traceback_threads(int fd, PyInterpreterState* interp, PyThreadState* current_tstate)
{
    resolve_dump_traceback_symbol();

    if (_Py_DumpTracebackThreads_ptr) {
        // Use the internal API if available (local development builds)
        return _Py_DumpTracebackThreads_ptr(fd, interp, current_tstate);
    } else {
        // Fallback: Use Python's faulthandler module API (manylinux builds)
        PyObject* faulthandler_module = PyImport_ImportModule("faulthandler");
        if (faulthandler_module == NULL) {
            PyErr_Clear();
            return "Failed to import faulthandler module";
        }

        PyObject* dump_func = PyObject_GetAttrString(faulthandler_module, "dump_traceback");
        Py_DECREF(faulthandler_module);

        if (dump_func == NULL) {
            PyErr_Clear();
            return "Failed to get dump_traceback function";
        }

        // Create a file object from the file descriptor
        PyObject* file_obj = PyLong_FromLong(fd);
        PyObject* all_threads = Py_True;
        Py_INCREF(all_threads);

        PyObject* result = PyObject_CallFunctionObjArgs(dump_func, file_obj, all_threads, NULL);

        Py_DECREF(dump_func);
        Py_DECREF(file_obj);
        Py_DECREF(all_threads);

        if (result == NULL) {
            PyErr_Clear();
            return "Failed to call faulthandler.dump_traceback";
        }

        Py_DECREF(result);
        return NULL; // Success
    }
}

PyThreadState*
crashtracker_get_current_tstate(void)
{
    return PyGILState_GetThisThreadState();
}