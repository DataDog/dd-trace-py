#pragma once

#include <Python.h>

#if PY_VERSION_HEX >= 0x030d0000
#define _PY313_AND_LATER
#endif

#if PY_VERSION_HEX >= 0x030c0000
#define _PY312_AND_LATER
#endif

#if PY_VERSION_HEX >= 0x030b0000
#define _PY311_AND_LATER
#endif

#if PY_VERSION_HEX >= 0x030a0000
#define _PY310_AND_LATER
#endif

#if PY_VERSION_HEX >= 0x03090000
#define _PY39_AND_LATER
#endif

#if PY_VERSION_HEX >= 0x03080000
#define _PY38_AND_LATER
#endif

#if PY_MAJOR_VERSION == 3 && PY_MINOR_VERSION == 8
#define _PY38
#endif
