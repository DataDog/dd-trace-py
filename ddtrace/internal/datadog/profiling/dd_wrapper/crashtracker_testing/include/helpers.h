#include <Python.h>

#ifndef LIB_NAME
#error "LIB_NAME is not defined"
#endif

#define CONCATENATE_DETAIL(x, y) x##y
#define CONCATENATE(x, y) CONCATENATE_DETAIL(x, y)
#define PYINIT_NAME(name) CONCATENATE(PyInit_, name)
#define MOD_STR(name) #name

