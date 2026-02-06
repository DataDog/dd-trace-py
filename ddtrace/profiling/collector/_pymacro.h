#pragma once

#include <Python.h>

#if PY_VERSION_HEX >= 0x030d0000
#define _PY313_AND_LATER
#endif

#if PY_VERSION_HEX >= 0x030c0000
#define _PY312_AND_LATER
#endif

#if PY_VERSION_HEX >= 0x030a0000
#define _PY310_AND_LATER
#endif
