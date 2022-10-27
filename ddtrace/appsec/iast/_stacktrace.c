#include <Python.h>
#include <frameobject.h>
#include <patchlevel.h>

#ifdef _WIN32
#define DD_TRACE_INSTALLED_PREFIX "\\ddtrace\\"
#define TESTS_PREFIX "\\tests\\"
#else
#define DD_TRACE_INSTALLED_PREFIX "/ddtrace/"
#define TESTS_PREFIX "/tests/"
#endif

#if PY_VERSION_HEX >= 0x30b00f0
#include <internal/pycore_frame.h>
#define PYTHON311
#endif

#ifdef PYTHON311
#define FrameType _PyCFrame
#define get_frame(tstate) tstate->cframe
#define get_previous(frame) frame->previous
#define get_filename(frame) frame->current_frame->f_code->co_filename
#define get_lineno(frame) frame->current_frame->frame_obj->f_lineno
#else
#define FrameType PyFrameObject
#define get_frame(tstate) tstate->frame
#define get_previous(frame) frame->f_back
#define get_filename(frame) frame->f_code->co_filename
#define get_lineno(frame) PyCode_Addr2Line(frame->f_code, frame->f_lasti)
#endif

/**
 * get_file_and_line
 *
 * Get the filename (path + filename) and line number of the original wrapped function to report it.
 *
 * @return Tuple, string and integer.
 **/
static PyObject*
get_file_and_line(PyObject* Py_UNUSED(module), PyObject* Py_UNUSED(args))
{
    PyThreadState* tstate = PyThreadState_GET();
    FrameType* frame;

    if (NULL != tstate && NULL != get_frame(tstate)) {
        frame = get_frame(tstate);
        while (NULL != frame) {
            char* filename = PyBytes_AsString(PyUnicode_AsEncodedString(get_filename(frame), "utf-8", "surrogatepass"));
            if (strstr(filename, DD_TRACE_INSTALLED_PREFIX) != NULL && strstr(filename, TESTS_PREFIX) == NULL) {
                frame = get_previous(frame);
                continue;
            }
            /*
             frame->f_lineno will not always return the correct line number
             you need to call PyCode_Addr2Line().
            */
            int line = get_lineno(frame);
            return PyTuple_Pack(2, get_filename(frame), Py_BuildValue("i", line));
        }
    }
    return PyTuple_Pack(2, Py_None, Py_None);
}

static PyMethodDef StacktraceMethods[] = {
    { "get_info_frame", (PyCFunction)get_file_and_line, METH_VARARGS, "stacktrace functions" },
    { NULL, NULL, 0, NULL }
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
    if (m == NULL)
        return NULL;
    return m;
}