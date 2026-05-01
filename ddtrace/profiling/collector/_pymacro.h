#pragma once

#include <Python.h>

/* Force-inline attribute for hot-path functions that must be inlined into
 * callers (e.g., the allocation sampling fast path). */
#if defined(__GNUC__) || defined(__clang__)
#define MEMALLOC_ALWAYS_INLINE inline __attribute__((always_inline))
#else
#define MEMALLOC_ALWAYS_INLINE inline
#endif

#if PY_VERSION_HEX >= 0x030e0000
#define _PY314_AND_LATER
#endif // PY_VERSION_HEX >= 0x030e0000

#if PY_VERSION_HEX >= 0x030d0000
#define _PY313_AND_LATER
#endif // PY_VERSION_HEX >= 0x030d0000

#if PY_VERSION_HEX >= 0x030c0000
#define _PY312_AND_LATER
#endif // PY_VERSION_HEX >= 0x030c0000

#if PY_VERSION_HEX >= 0x030b0000
#define _PY311_AND_LATER
#endif // PY_VERSION_HEX >= 0x030b0000

#if PY_VERSION_HEX >= 0x030a0000
#define _PY310_AND_LATER
#endif // PY_VERSION_HEX >= 0x030a0000
