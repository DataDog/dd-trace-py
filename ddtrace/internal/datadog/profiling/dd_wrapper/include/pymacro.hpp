#pragma once

#include <Python.h>

#if PY_VERSION_HEX >= 0x030c0000
#define _PY312_AND_LATER
#endif

#if PY_VERSION_HEX >= 0x030b0000
#define _PY311_AND_LATER
#endif
