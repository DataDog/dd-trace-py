#pragma once

#include <Python.h>

/* Branch prediction hints for hot paths.
 * Use MEMALLOC_LIKELY when the condition is almost always true (e.g., successful allocation).
 * Use MEMALLOC_UNLIKELY when the condition is almost always false (e.g., NULL pointer, error path). */
#if defined(__GNUC__) || defined(__clang__)
#define MEMALLOC_LIKELY(x) __builtin_expect(!!(x), 1)
#define MEMALLOC_UNLIKELY(x) __builtin_expect(!!(x), 0)
#define MEMALLOC_ALWAYS_INLINE inline __attribute__((always_inline))
#else
#define MEMALLOC_LIKELY(x) (x)
#define MEMALLOC_UNLIKELY(x) (x)
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
