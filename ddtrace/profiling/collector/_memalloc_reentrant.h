#ifndef _DDTRACE_MEMALLOC_REENTRANT_H
#define _DDTRACE_MEMALLOC_REENTRANT_H

#include <stdbool.h>

// This is a simple thread-local reentrance guard.
// Note the use of dynamic TLS.  Since this is a dynamic library it isn't in control of how much static TLS is
// available, since that is determined at load-time by the main executable.  We've already had issues with popping
// some users' static TLS limits, so use dynamic TLS for now.

// NB, this is actually set in the corresponding .c to prevent multiple defs
// Cross-platform thread-local macro
#if defined(_MSC_VER) // Check for MSVC compiler
#define MEMALLOC_TLS __declspec(thread)
#elif defined(__GNUC__) || defined(__clang__) // GCC or Clang
#define MEMALLOC_TLS __attribute__((tls_model("global-dynamic"))) __thread
#else
#error "Unsupported compiler for thread-local storage"
#endif
extern MEMALLOC_TLS bool _MEMALLOC_ON_THREAD;

static inline void
memalloc_set_reentrant(bool reentrant)
{
    // A get/set type guard doesn't manage the internal state, so it's susceptible to issues in the external logic.
    _MEMALLOC_ON_THREAD = reentrant;
}

static inline bool
memalloc_get_reentrant(void)
{
    return _MEMALLOC_ON_THREAD;
}

#endif
