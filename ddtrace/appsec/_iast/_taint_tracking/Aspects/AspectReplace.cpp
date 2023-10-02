#include "AspectReplace.h"

PyObject*
api_replace_aspect(PyObject* orig_str, PyObject* substr, PyObject* replstr, Py_ssize_t maxcount)
{
    return PyUnicode_Replace(orig_str, substr, replstr, maxcount);
}
