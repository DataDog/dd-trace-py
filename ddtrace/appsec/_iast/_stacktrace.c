#include <Python.h>
#include <frameobject.h>
#include <patchlevel.h>
#include <stdbool.h>

#ifdef _WIN32
#define DD_TRACE_INSTALLED_PREFIX "\\ddtrace\\"
#define TESTS_PREFIX "\\tests\\"
#define SITE_PACKAGES_PREFIX "\\site-packages\\"
#else
#define DD_TRACE_INSTALLED_PREFIX "/ddtrace/"
#define TESTS_PREFIX "/tests/"
#define SITE_PACKAGES_PREFIX "/site-packages/"
#endif

#if PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION >= 11
#include <internal/pycore_frame.h>
#define GET_LINENO(frame) PyFrame_GetLineNumber((PyFrameObject*)frame)
#define GET_FRAME(tstate) PyThreadState_GetFrame(tstate)
#define GET_PREVIOUS(frame) PyFrame_GetBack(frame)
#define FRAME_INCREF(frame) Py_INCREF(frame)
#define FRAME_DECREF(frame) Py_DecRef(frame)
#define FRAME_XDECREF(frame) Py_XDECREF(frame)
#define FILENAME_DECREF(filename) Py_DecRef(filename)
#define FILENAME_XDECREF(filename)                                                                                     \
    if (filename)                                                                                                      \
    Py_DecRef(filename)
static inline PyObject*
GET_FILENAME(PyFrameObject* frame)
{
    PyCodeObject* code = PyFrame_GetCode(frame);
    if (!code) {
        return NULL;
    }
    PyObject* filename = PyObject_GetAttrString((PyObject*)code, "co_filename");
    Py_DecRef(code);
    return filename;
}
#else
#define GET_FRAME(tstate) tstate->frame
#define GET_PREVIOUS(frame) frame->f_back
#define GET_FILENAME(frame) frame->f_code->co_filename
#define FRAME_INCREF(frame)
#define FRAME_DECREF(frame)
#define FRAME_XDECREF(frame)
#define FILENAME_DECREF(filename)
#define FILENAME_XDECREF(filename)
#if PY_MAJOR_VERSION >= 3 && PY_MINOR_VERSION >= 10
/* See: https://bugs.python.org/issue44964 */
#define GET_LINENO(frame) PyCode_Addr2Line(frame->f_code, frame->f_lasti * 2)
#else
#define GET_LINENO(frame) PyCode_Addr2Line(frame->f_code, frame->f_lasti)
#endif
#endif

// Python standard library path
static char* STDLIB_PATH = NULL;
static ssize_t STDLIB_PATH_LEN = 0;

// Python site-packages path
static char* PURELIB_PATH = NULL;
static ssize_t PURELIB_PATH_LEN = 0;

/**
 * Checks if the filename is special.
 * For example, a frozen module (`<frozen 'os'>`), a template (`<template>`), etc.
 */
static inline bool
_is_special_frame(const char* filename)
{
    return filename && strncmp(filename, "<", strlen("<")) == 0;
}

static inline bool
_is_ddtrace_filename(const char* filename)
{
    return filename && strstr(filename, DD_TRACE_INSTALLED_PREFIX) != NULL && strstr(filename, TESTS_PREFIX) == NULL;
}

static inline bool
_is_site_packages_filename(const char* filename)
{
    const bool res = filename && PURELIB_PATH && strncmp(filename, PURELIB_PATH, PURELIB_PATH_LEN) == 0;
    return res;
}

static inline bool
_is_stdlib_filename(const char* filename)
{
    // site-packages is often a subdirectory of stdlib directory, so stdlib
    // path is defined as prefixed by stdlib and not prefixed by purelib.
    // TODO: As of Python 3.10, we could use sys.stdlib_module_names.
    const bool res = filename && STDLIB_PATH && !_is_site_packages_filename(filename) &&
                     strncmp(filename, STDLIB_PATH, STDLIB_PATH_LEN) == 0;
    return res;
}

static char*
get_sysconfig_path(const char* name)
{
    PyObject* sysconfig_mod = PyImport_ImportModule("sysconfig");
    if (!sysconfig_mod) {
        return NULL;
    }

    PyObject* path = PyObject_CallMethod(sysconfig_mod, "get_path", "s", name);
    if (!path) {
        Py_DECREF(sysconfig_mod);
        return NULL;
    }

    const char* path_str = PyUnicode_AsUTF8(path);
    char* res = NULL;
    if (path_str) {
        res = strdup(path_str);
    }
    Py_DECREF(path);
    Py_DECREF(sysconfig_mod);
    return res;
}

/**
 * Gets a reference to a PyFrameObject and walks up the stack until a relevant frame is found.
 *
 * Returns a new reference to the PyFrameObject.
 *
 * The caller is not responsible for DECREF'ing the given PyFrameObject, but it is responsible for
 * DECREF'ing the returned PyFrameObject.
 */
static PyFrameObject*
_find_relevant_frame(PyFrameObject* frame, bool allow_site_packages)
{
    while (NULL != frame) {
        PyObject* filename_o = GET_FILENAME(frame);
        if (!filename_o) {
            FRAME_DECREF(frame);
            return NULL;
        }
        const char* filename = PyUnicode_AsUTF8(filename_o);
        if (_is_special_frame(filename) || _is_ddtrace_filename(filename) || _is_stdlib_filename(filename) ||
            (!allow_site_packages && _is_site_packages_filename(filename))) {
            PyFrameObject* prev_frame = GET_PREVIOUS(frame);
            FRAME_DECREF(frame);
            FILENAME_DECREF(filename_o);
            frame = prev_frame;
            continue;
        }
        FILENAME_DECREF(filename_o);
        break;
    }
    return frame;
}

static PyObject*
_get_result_tuple(PyFrameObject* frame)
{
    PyObject* result = NULL;
    PyObject* filename_o = NULL;
    PyObject* line_o = NULL;
    PyObject* funcname_o = NULL;
    PyObject* classname_o = NULL;

    filename_o = GET_FILENAME(frame);
    if (!filename_o) {
        goto error;
    }

    // frame->f_lineno will not always return the correct line number
    // you need to call PyCode_Addr2Line().
    int line = GET_LINENO(frame);
    line_o = Py_BuildValue("i", line);
    if (!line_o) {
        goto error;
    }
    result = PyTuple_Pack(2, filename_o, line_o);

error:
    FILENAME_XDECREF(filename_o);
    Py_XDECREF(line_o);
    return result;
}

/**
 * get_file_and_line
 *
 * Get the filename (path + filename) and line number of the original wrapped
 *function to report it.
 *
 * @return Tuple, string and integer.
 **/
static PyObject*
get_file_and_line(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    PyFrameObject* frame = NULL;
    PyFrameObject* backup_frame = NULL;
    PyObject* result = NULL;
    PyThreadState* tstate = PyThreadState_Get();
    if (!tstate) {
        goto exit;
    }

    frame = GET_FRAME(tstate);
    if (!frame) {
        goto exit;
    }

    // Skip all frames until the first non-ddtrace and non-stdlib frame.
    // Store that frame as backup (if any). If there is no better frame, fallback to this.
    // This happens, for example, when the vulnerability is in a package installed in site-packages.
    frame = _find_relevant_frame(frame, true);
    if (NULL == frame) {
        goto exit;
    }
    backup_frame = frame;
    FRAME_INCREF(backup_frame);

    // Continue skipping until we find a frame that is both non-ddtrace and non-site-packages.
    frame = _find_relevant_frame(frame, false);
    if (NULL == frame) {
        frame = backup_frame;
        backup_frame = NULL;
    } else {
        FRAME_DECREF(backup_frame);
    }

    result = _get_result_tuple(frame);

exit:
    FRAME_XDECREF(frame);
    return result;
}

static PyMethodDef StacktraceMethods[] = { { "get_info_frame",
                                             (PyCFunction)get_file_and_line,
                                             METH_NOARGS,
                                             "Stacktrace function: returns (filename, line, method, class)" },
                                           { NULL, NULL, 0, NULL } };

static struct PyModuleDef stacktrace = { PyModuleDef_HEAD_INIT,
                                         "ddtrace.appsec._iast._stacktrace",
                                         "stacktrace module",
                                         -1,
                                         StacktraceMethods };

PyMODINIT_FUNC
PyInit__stacktrace(void)
{
    PyObject* m = PyModule_Create(&stacktrace);
    if (m == NULL)
        return NULL;
    STDLIB_PATH = get_sysconfig_path("stdlib");
    if (STDLIB_PATH) {
        STDLIB_PATH_LEN = strlen(STDLIB_PATH);
    }
    PURELIB_PATH = get_sysconfig_path("purelib");
    if (PURELIB_PATH) {
        PURELIB_PATH_LEN = strlen(PURELIB_PATH);
    }
    return m;
}
