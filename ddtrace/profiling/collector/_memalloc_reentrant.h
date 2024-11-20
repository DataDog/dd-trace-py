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

// Now define a CAS primitive for use in the implementation
#if defined(_WIN32) || defined(_WIN64)
#include <windows.h>
static inline bool
cas_thread_local_bool(bool* target, bool expected, bool desired)
{
    LONG expected_long = (LONG)expected;
    LONG desired_long = (LONG)desired;
    return (LONG)expected == InterlockedCompareExchange((volatile LONG*)target, desired_long, expected_long);
}
#else
static inline bool
cas_thread_local_bool(bool* target, bool expected, bool desired)
{
    return __atomic_compare_exchange_n(target, &expected, &desired, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
#endif

static inline bool
memalloc_take_guard()
{
    return cas_thread_local_bool(&_MEMALLOC_ON_THREAD, false, true);
}

static inline bool
memalloc_yield_guard(void)
{
    // Ideally, we'd actually capture the old state within an object and restore it, but since this is
    // a coarse-grained lock, we just set it to false.
    _MEMALLOC_ON_THREAD = false;
}

#endif
