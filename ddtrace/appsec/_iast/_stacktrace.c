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

/**
 * get_file_and_line
 *
 * Get the filename (path + filename) and line number of the original wrapped
 *function to report it.
 *
 * @return Tuple, string and integer.
 **/
static PyObject*
get_file_and_line(PyObject* Py_UNUSED(module), PyObject* cwd_obj)
{
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
        result = PyTuple_Pack(2, filename_o, line_obj);
        break;
    }
    if (result == NULL) {
        goto exit_0;
    }

exit:
    Py_DecRef(cwd_bytes);
    FRAME_XDECREF(frame);
    FILENAME_XDECREF(filename_o);
    return result;

exit_0:; // fix: "a label can only be part of a statement and a declaration is not a statement" error
    // Return "", -1
    PyObject* line_obj = Py_BuildValue("i", -1);
    filename_o = PyUnicode_FromString("");
    result = PyTuple_Pack(2, filename_o, line_obj);
    Py_DecRef(cwd_bytes);
    FRAME_XDECREF(frame);
    FILENAME_XDECREF(filename_o);
    Py_DecRef(line_obj);
    return result;
}

static PyMethodDef StacktraceMethods[] = {
    { "get_info_frame", (PyCFunction)get_file_and_line, METH_O, "stacktrace functions" },
    { NULL, NULL, 0, NULL }
};

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
