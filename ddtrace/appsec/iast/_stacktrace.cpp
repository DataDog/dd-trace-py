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
#define FrameType _PyCFrame
#define GET_FRAME(tstate) PyThreadState_GetFrame(tstate)
#define GET_PREVIOUS(frame) frame->previous
#define GET_FILENAME(frame) frame->current_frame->f_code->co_filename
#define GET_LINENO(frame)                                                                                              \
    PyCode_Addr2Line(frame->current_frame->f_code, PyFrame_GetLasti(_PyFrame_GetFrameObject(frame)))
#else
#define FrameType PyFrameObject
#define GET_FRAME(tstate) tstate->frame
#define GET_PREVIOUS(frame) frame->f_back
#define GET_FILENAME(frame) frame->f_code->co_filename
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
 * Get the filename (path + filename) and line number of the original wrapped function to report it.
 *
 * @return Tuple, string and integer.
 **/
static PyObject*
get_file_and_line(PyObject* Py_UNUSED(module), PyObject* args)
{
    PyThreadState* tstate = PyThreadState_GET();
    FrameType* frame;
    PyObject* filename_o;
    char* filename;
    int line;

    PyObject *cwd_obj = Py_None, *cwd_bytes;
    char* cwd;
    if (!PyArg_ParseTuple(args, "O", &cwd_obj))
        return nullptr;
    if (cwd_obj != Py_None) {
        if (!PyUnicode_FSConverter(cwd_obj, &cwd_bytes))
            return nullptr;
        cwd = PyBytes_AsString(cwd_bytes);
    } else {
        return nullptr;
    }

    if (nullptr != tstate && nullptr != GET_FRAME(tstate)) {
        frame = GET_FRAME(tstate);
        while (nullptr != frame) {
            filename_o = GET_FILENAME(frame);
            filename = PyBytes_AsString(PyUnicode_AsEncodedString(filename_o, "utf-8", "surrogatepass"));
            if ((strstr(filename, DD_TRACE_INSTALLED_PREFIX) != nullptr && strstr(filename, TESTS_PREFIX) == nullptr) ||
                strstr(filename, SITE_PACKAGES_PREFIX) != nullptr || strstr(filename, cwd) == nullptr) {
                frame = GET_PREVIOUS(frame);
                continue;
            }
            /*
             frame->f_lineno will not always return the correct line number
             you need to call PyCode_Addr2Line().
            */
            line = GET_LINENO(frame);

            return PyTuple_Pack(2, filename_o, Py_BuildValue("i", line));
        }
    }
#if PY_MAJOR_VERSION > 3 || PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION >= 10
    return Py_NewRef(Py_None);
#else
    Py_INCREF(Py_None);
    return Py_None;
#endif
}

static PyMethodDef StacktraceMethods[] = {
    { "get_info_frame", (PyCFunction)get_file_and_line, METH_VARARGS, "stacktrace functions" },
    { nullptr, nullptr, 0, nullptr }
};

static struct PyModuleDef stacktrace = { PyModuleDef_HEAD_INIT,
                                         "ddtrace.appsec.iast._stacktrace",
                                         "stacktrace module",
                                         -1,
                                         StacktraceMethods };

PyMODINIT_FUNC
PyInit__stacktrace(void)
{
    PyObject* m;
    m = PyModule_Create(&stacktrace);
    if (m == nullptr)
        return nullptr;
    return m;
}
