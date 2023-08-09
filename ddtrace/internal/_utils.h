#include <string.h>

// Determine if the provided object can be treated as a `bytes`
static inline int
PyBytesLike_Check(PyObject* o)
{
    return PyBytes_Check(o) || PyByteArray_Check(o);
}
