// Reading a frame's locals can run user code, which may land back in
// get_info_frame. This marks the native stack walk so a nested call gives up
// instead of recursing.
#if defined(_MSC_VER)
__declspec(thread) static int in_stacktrace = 0;
#else
static __thread int in_stacktrace = 0;
#endif

#include <Python.h>
#include <frameobject.h>
#include <patchlevel.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#if PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION >= 11
#include <internal/pycore_frame.h>
#define GET_LINENO(frame) PyFrame_GetLineNumber((PyFrameObject*)frame)
#define GET_FRAME(tstate) PyThreadState_GetFrame(tstate)
#define GET_PREVIOUS(frame) PyFrame_GetBack(frame)
#define FRAME_INCREF(frame) Py_INCREF((PyObject*)frame)
#define FRAME_DECREF(frame) Py_DecRef((PyObject*)frame)
#define FRAME_XDECREF(frame) Py_XDECREF((PyObject*)frame)
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
    Py_DecRef((PyObject*)code);
    return filename;
}

static inline PyObject*
GET_LOCALS(PyFrameObject* frame)
{
    return PyFrame_GetLocals(frame);
}

static inline PyObject*
GET_FUNCTION(PyFrameObject* frame)
{
    PyCodeObject* code = PyFrame_GetCode(frame);
    if (!code) {
        return PyUnicode_FromString("");
    }
    PyObject* func = PyObject_GetAttrString((PyObject*)code, "co_name");
    Py_DecRef((PyObject*)code);
    if (!func) {
        return PyUnicode_FromString("");
    }
    return func;
}

#else
#define GET_FRAME(tstate) tstate->frame
#define GET_PREVIOUS(frame) frame->f_back
#define GET_FILENAME(frame) ((PyObject*)(frame->f_code->co_filename))
#define FRAME_INCREF(frame)
#define FRAME_DECREF(frame)
#define FRAME_XDECREF(frame)
#define FILENAME_DECREF(filename)
#define FILENAME_XDECREF(filename)
/* FastToLocals populates frame->f_locals before we read it, and we take our own
 * reference so the caller releases locals the same way on every version. */
static inline PyObject*
GET_LOCALS(PyFrameObject* frame)
{
    if (PyFrame_FastToLocalsWithError(frame) < 0) {
        // Best effort: a frame whose locals cannot be materialised simply has no
        // class name, and leaving the error pending would surface it later.
        PyErr_Clear();
        return NULL;
    }
    Py_XINCREF(frame->f_locals);
    return frame->f_locals;
}
static inline PyObject*
GET_FUNCTION(PyFrameObject* frame)
{
    PyObject* func = frame->f_code->co_name;
    Py_INCREF(func);
    return func;
}
#if PY_MAJOR_VERSION >= 3 && PY_MINOR_VERSION >= 10
/* See: https://bugs.python.org/issue44964 */
#define GET_LINENO(frame) PyCode_Addr2Line(frame->f_code, frame->f_lasti * 2)
#else
#define GET_LINENO(frame) PyCode_Addr2Line(frame->f_code, frame->f_lasti)
#endif
#endif

/* A directory prefix in both the form the interpreter reports and its resolved
 * form.
 *
 * sysconfig reports the symlinked layout on some distributions -- homebrew gives
 * /opt/homebrew/opt/... while co_filename carries /opt/homebrew/Cellar/... -- so a
 * single string misses every frame under it. Resolving here, once, keeps the
 * per-frame check free of syscalls.
 */
typedef struct
{
    char* path;
    ssize_t path_len;
    char* resolved;
    ssize_t resolved_len;
} path_root_t;

// Python standard library path
static path_root_t STDLIB_ROOT = { NULL, 0, NULL, 0 };

// Python site-packages path
static path_root_t PURELIB_ROOT = { NULL, 0, NULL, 0 };

// ddtrace module path
static path_root_t DDTRACE_ROOT = { NULL, 0, NULL, 0 };

// The separate ddtrace-internal distribution uses the _ddtrace_internal package.
static path_root_t DDTRACE_INTERNAL_ROOT = { NULL, 0, NULL, 0 };

/* True when `filename` is `root` itself or sits directly under it. The separator
 * check keeps /srv/ddtrace-tools/x.py out of /srv/ddtrace. */
static inline bool
_is_prefixed_by(const char* filename, const char* root, ssize_t root_len)
{
    if (strncmp(filename, root, root_len) != 0) {
        return false;
    }
    const char next = filename[root_len];
    return next == '\0' || next == '/' || next == '\\';
}

static inline bool
_is_under(const char* filename, const path_root_t* root)
{
    if (!filename || !root->path) {
        return false;
    }
    return _is_prefixed_by(filename, root->path, root->path_len) ||
           (root->resolved != NULL && _is_prefixed_by(filename, root->resolved, root->resolved_len));
}

/* Takes ownership of `path`. */
static void
path_root_set(path_root_t* root, char* path)
{
    if (!path) {
        return;
    }
    root->path = path;
    root->path_len = strlen(path);

#if !defined(_WIN32)
    char* resolved = realpath(path, NULL);
    if (resolved != NULL && strcmp(resolved, path) != 0) {
        root->resolved = resolved;
        root->resolved_len = strlen(resolved);
    } else {
        free(resolved);
    }
#endif
}

/* PyFrame_GetLocals returns a dict up to 3.12 and a PyFrameLocalsProxy from 3.13,
 * so look the name up through the mapping protocol rather than
 * PyDict_GetItemString. Returns a new reference, or NULL when the frame has no
 * `self`. */
static inline PyObject*
GET_SELF(PyObject* locals)
{
    PyObject* key = PyUnicode_InternFromString("self");
    if (!key) {
        PyErr_Clear();
        return NULL;
    }
    PyObject* self_obj = PyObject_GetItem(locals, key);
    Py_DecRef(key);
    if (!self_obj) {
        // A plain function has no `self`; the KeyError is not an error here.
        PyErr_Clear();
    }
    return self_obj;
}

static inline PyObject*
GET_TYPE_NAME(PyTypeObject* type)
{
#if PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION >= 11
    return PyType_GetName(type);
#else
    // Match PyType_GetName without invoking a metaclass's attribute hooks.
    if (type->tp_flags & Py_TPFLAGS_HEAPTYPE) {
        PyObject* name = ((PyHeapTypeObject*)type)->ht_name;
        Py_INCREF(name);
        return name;
    }
    const char* name = strrchr(type->tp_name, '.');
    return PyUnicode_FromString(name ? name + 1 : type->tp_name);
#endif
}

static inline PyObject*
GET_CLASS(PyFrameObject* frame)
{
    if (frame) {
        PyObject* locals = GET_LOCALS(frame);
        if (locals) {
            PyObject* self_obj = GET_SELF(locals);
            if (self_obj) {
                PyObject* class_name = GET_TYPE_NAME(Py_TYPE(self_obj));
                Py_DecRef(self_obj);
                if (class_name) {
                    Py_DecRef(locals);
                    return class_name;
                }
            }
            Py_DecRef(locals);
        }
    }
    // Class metadata is best effort; its failure must not discard the location.
    PyErr_Clear();
    return PyUnicode_FromString("");
}

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
    return _is_under(filename, &DDTRACE_ROOT) || _is_under(filename, &DDTRACE_INTERNAL_ROOT);
}

static inline bool
_is_site_packages_filename(const char* filename)
{
    // Any `site-packages` path component also covers user site directories and
    // venvs whose purelib differs from the running interpreter.
    return filename && (strstr(filename, "/site-packages/") || strstr(filename, "\\site-packages\\") ||
                        _is_under(filename, &PURELIB_ROOT));
}

static inline bool
_is_stdlib_filename(const char* filename)
{
    // site-packages is often a subdirectory of stdlib directory, so stdlib
    // path is defined as prefixed by stdlib and not prefixed by purelib.
    // TODO: As of Python 3.10, we could use sys.stdlib_module_names.
    return !_is_site_packages_filename(filename) && _is_under(filename, &STDLIB_ROOT);
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

static char*
get_package_path(const char* package_name)
{
    char* res = NULL;
    PyObject* package = NULL;
    PyObject* path = NULL;

    package = PyImport_ImportModule(package_name);
    if (!package) {
        goto exit;
    }

    path = PyObject_GetAttrString(package, "__file__");
    if (!path) {
        goto exit;
    }

    const char* path_str = PyUnicode_AsUTF8(path);
    if (path_str) {
        const char* separator = strrchr(path_str, '/');
        const char* windows_separator = strrchr(path_str, '\\');
        if (windows_separator && (!separator || windows_separator > separator)) {
            separator = windows_separator;
        }
        if (separator) {
            res = strndup(path_str, separator - path_str);
        }
    }

exit:
    Py_XDECREF(path);
    Py_XDECREF(package);
    // A missing package root should not prevent the collector from loading.
    PyErr_Clear();
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
    funcname_o = GET_FUNCTION(frame);
    if (!funcname_o) {
        goto error;
    }
    classname_o = GET_CLASS(frame);
    if (!classname_o) {
        goto error;
    }
    result = PyTuple_Pack(4, filename_o, line_o, funcname_o, classname_o);

error:
    FILENAME_XDECREF(filename_o);
    Py_XDECREF(line_o);
    Py_XDECREF(funcname_o);
    Py_XDECREF(classname_o);
    return result;
}

/**
 * get_file_and_line
 *
 * Get the filename, line number, function name and class name of the original wrapped
 * function to report it.
 *
 * Returns a tuple:
 *     (filename, line_number, function name, class name)
 **/
static PyObject*
get_file_and_line(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    // Reading a frame's locals can run user code, which may land back here. The
    // nested call gives up rather than recursing.
    if (in_stacktrace) {
        return PyTuple_Pack(4, Py_None, Py_None, Py_None, Py_None);
    }
    in_stacktrace = 1;

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
    if (!result) {
        result = PyTuple_Pack(4, Py_None, Py_None, Py_None, Py_None);
    }
    in_stacktrace = 0;
    return result;
}

static PyMethodDef StacktraceMethods[] = { { "get_info_frame",
                                             (PyCFunction)get_file_and_line,
                                             METH_NOARGS,
                                             "Stacktrace function: returns (filename, line, method, class)" },
                                           { NULL, NULL, 0, NULL } };

static struct PyModuleDef stacktrace = { PyModuleDef_HEAD_INIT,
                                         "ddtrace.appsec._shared._stacktrace",
                                         "stacktrace module",
                                         -1,
                                         StacktraceMethods };

PyMODINIT_FUNC
PyInit__stacktrace(void)
{
    PyObject* m = PyModule_Create(&stacktrace);
    if (m == NULL)
        return NULL;
    path_root_set(&STDLIB_ROOT, get_sysconfig_path("stdlib"));
    path_root_set(&PURELIB_ROOT, get_sysconfig_path("purelib"));
    path_root_set(&DDTRACE_ROOT, get_package_path("ddtrace"));
    path_root_set(&DDTRACE_INTERNAL_ROOT, get_package_path("_ddtrace_internal"));
    return m;
}
