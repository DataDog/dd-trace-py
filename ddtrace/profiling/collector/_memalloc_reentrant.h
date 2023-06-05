#ifndef _DDTRACE_MEMALLOC_REENTRANT_H
#define _DDTRACE_MEMALLOC_REENTRANT_H

#include "_pymacro.h"
#include <stdbool.h>

#ifndef _PY37_AND_LATER
#include <pythread.h>
#endif

#ifdef _PY37_AND_LATER
extern Py_tss_t memalloc_reentrant_key;
#else
extern int memalloc_reentrant_key;
#endif

/* Any non-NULL pointer can be used */
#define _MEMALLOC_REENTRANT_VALUE Py_True

static inline void
memalloc_set_reentrant(bool reentrant)
{
    if (reentrant)
#ifdef _PY37_AND_LATER
        PyThread_tss_set(&memalloc_reentrant_key, _MEMALLOC_REENTRANT_VALUE);
#else
        PyThread_set_key_value(memalloc_reentrant_key, _MEMALLOC_REENTRANT_VALUE);
#endif
    else
#ifdef _PY37_AND_LATER
        PyThread_tss_set(&memalloc_reentrant_key, NULL);
#else
        PyThread_set_key_value(memalloc_reentrant_key, NULL);
#endif
}

static inline bool
memalloc_get_reentrant(void)
{
#ifdef _PY37_AND_LATER
    if (PyThread_tss_get(&memalloc_reentrant_key))
#else
    if (PyThread_get_key_value(memalloc_reentrant_key))
#endif
        return true;

    return false;
}

#endif
