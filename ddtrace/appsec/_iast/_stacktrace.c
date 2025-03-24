// Reading locals can trigger side effects; this protects about recursive reading of the locals potentially
// caused by them
#if defined(_MSC_VER)
__declspec(thread) static int in_stacktrace = 0;
#else
static __thread int in_stacktrace = 0;
#endif

#include <Python.h>
#include <frameobject.h>
#include <patchlevel.h>

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
#define FRAME_DECREF(frame)
#define FRAME_XDECREF(frame)
#define FILENAME_DECREF(filename)
#define FILENAME_XDECREF(filename)
#define GET_LOCALS(frame) ((PyObject*)(frame->f_locals))
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

static inline PyObject*
SAFE_GET_LOCALS(PyFrameObject* frame)
{
    if (in_stacktrace) {
        // Return a nullptr to avoid triggering reentrant native calls.
        return NULL;
    }
    return GET_LOCALS(frame);
}

static inline PyObject*
GET_CLASS(PyFrameObject* frame)
{
    if (frame) {
        PyObject* locals = SAFE_GET_LOCALS(frame);
        if (locals) {
            PyObject* self_obj = PyDict_GetItemString(locals, "self");
            if (self_obj) {
                PyObject* self_class = PyObject_GetAttrString(self_obj, "__class__");
                if (self_class) {
                    PyObject* class_name = PyObject_GetAttrString(self_class, "__name__");
                    Py_DecRef(self_class);
                    if (class_name) {
                        return class_name;
                    }
                }
            }
        }
    }
    return PyUnicode_FromString("");
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
get_file_and_line(PyObject* Py_UNUSED(module), PyObject* cwd_obj)
{
    // Mark that we are now capturing a stack trace to avoid reentrant calls on GET_LOCALS
    in_stacktrace = 1;

    PyThreadState* tstate = PyThreadState_Get();
    if (!tstate) {
        goto exit_0;
    }

    int line;
    PyObject* filename_o = NULL;
    PyObject* result = NULL;
    PyObject* cwd_bytes = NULL;
    char* cwd = NULL;

    if (!PyUnicode_FSConverter(cwd_obj, &cwd_bytes)) {
        goto exit_0;
    }
    cwd = PyBytes_AsString(cwd_bytes);
    if (!cwd) {
        goto exit_0;
    }

    PyFrameObject* frame = GET_FRAME(tstate);
    if (!frame) {
        goto exit_0;
    }

    while (NULL != frame) {
        filename_o = GET_FILENAME(frame);
        if (!filename_o) {
            goto exit;
        }
        const char* filename = PyUnicode_AsUTF8(filename_o);
        if (((strstr(filename, DD_TRACE_INSTALLED_PREFIX) != NULL && strstr(filename, TESTS_PREFIX) == NULL)) ||
            (strstr(filename, SITE_PACKAGES_PREFIX) != NULL || strstr(filename, cwd) == NULL)) {
            PyFrameObject* prev_frame = GET_PREVIOUS(frame);
            FRAME_DECREF(frame);
            FILENAME_DECREF(filename_o);
            frame = prev_frame;
            continue;
        }
        /*
         frame->f_lineno will not always return the correct line number
         you need to call PyCode_Addr2Line().
        */
        line = GET_LINENO(frame);
        PyObject* line_obj = Py_BuildValue("i", line);
        if (!line_obj) {
            goto exit;
        }
        PyObject* func_name = GET_FUNCTION(frame);
        if (!func_name) {
            Py_DecRef(line_obj);
            goto exit;
        }
        PyObject* class_name = GET_CLASS(frame);
        if (!class_name) {
            Py_DecRef(line_obj);
            Py_DecRef(func_name);
            goto exit;
        }
        result = PyTuple_Pack(4, filename_o, line_obj, func_name, class_name);
        Py_DecRef(func_name);
        Py_DecRef(class_name);
        Py_DecRef(line_obj);
        break;
    }
    if (result == NULL) {
        goto exit_0;
    }

exit:
    Py_DecRef(cwd_bytes);
    FRAME_XDECREF(frame);
    FILENAME_XDECREF(filename_o);
    in_stacktrace = 0;
    return result;

exit_0:; /* Label must be followed by a statement */
    // Return "", -1, "", ""
    PyObject* line_obj = Py_BuildValue("i", -1);
    filename_o = PyUnicode_FromString("");
    PyObject* func_name = PyUnicode_FromString("");
    PyObject* class_name = PyUnicode_FromString("");
    result = PyTuple_Pack(4, filename_o, line_obj, func_name, class_name);
    Py_DecRef(cwd_bytes);
    FRAME_XDECREF(frame);
    FILENAME_XDECREF(filename_o);
    Py_DecRef(line_obj);
    Py_DecRef(func_name);
    Py_DecRef(class_name);
    in_stacktrace = 0;
    return result;
}

static PyMethodDef StacktraceMethods[] = { { "get_info_frame",
                                             (PyCFunction)get_file_and_line,
                                             METH_O,
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
    return m;
}
