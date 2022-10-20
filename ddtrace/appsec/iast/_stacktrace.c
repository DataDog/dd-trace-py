#include <Python.h>
#include <frameobject.h>

#ifdef _WIN32
#define DD_TRACE_INSTALLED_PREFIX "\\ddtrace\\"
#define TESTS_PREFIX "\\tests\\"
#else
#define DD_TRACE_INSTALLED_PREFIX "/ddtrace/"
#define TESTS_PREFIX "/tests/"
#endif

/**
 * get_file_and_line
 *
 * Get the filename (path + filename) and line number of the original wrapped function to report it.
 *
 * @return Tuple, string and integer.
 **/
static PyObject*
get_file_and_line(PyObject* self, PyObject* const* args, Py_ssize_t nargs)
{
    PyThreadState* tstate = PyThreadState_GET();
    PyFrameObject* frame;
    if (NULL != tstate && NULL != tstate->frame) {
        frame = tstate->frame;
        while (NULL != frame) {
            char* filename =
              PyBytes_AsString(PyUnicode_AsEncodedString(frame->f_code->co_filename, "utf-8", "surrogatepass"));
            if (strstr(filename, DD_TRACE_INSTALLED_PREFIX) != NULL && strstr(filename, TESTS_PREFIX) == NULL) {
                frame = frame->f_back;
                continue;
            }
            /*
             frame->f_lineno will not always return the correct line number
             you need to call PyCode_Addr2Line().
            */
            int line = PyCode_Addr2Line(frame->f_code, frame->f_lasti);
            return PyTuple_Pack(2, frame->f_code->co_filename, Py_BuildValue("i", line));
        }
    }
    return PyTuple_Pack(2, Py_None, Py_None);
}

static PyMethodDef StacktraceMethods[] = {
    { "get_info_frame", ((PyCFunction)get_file_and_line), METH_FASTCALL, "stacktrace functions" },
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