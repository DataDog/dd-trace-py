#ifndef _DDTRACE_MEMALLOC_REENTRANT_H
#define _DDTRACE_MEMALLOC_REENTRANT_H

#ifdef _WIN32
#include <windows.h>
#else
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <time.h>
#include <unistd.h>
#endif
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

// Cross-platform macro for defining thread-local storage
#if defined(_MSC_VER) // Check for MSVC compiler
#define MEMALLOC_TLS __declspec(thread)
#elif defined(__GNUC__) || defined(__clang__) // GCC or Clang
// NB - we explicitly specify global-dynamic on Unix because the others are problematic.
// See e.g. https://fuchsia.dev/fuchsia-src/development/kernel/threads/tls for
// an explanation of thread-local storage access models. global-dynamic is the
// most general access model and is always correct to use, if not always the
// fastest, for a library like this which will be dlopened by an executable.
// The C toolchain should do the right thing without this attribute if it
// sees we're building a shared library. But we've been bit by issues related
// to this before, and it doesn't hurt to explicitly declare the model here.
#define MEMALLOC_TLS __attribute__((tls_model("global-dynamic"))) __thread
#else
#error "Unsupported compiler for thread-local storage"
#endif
extern MEMALLOC_TLS bool _MEMALLOC_ON_THREAD;

// This is a saturating atomic add for 32- and 64-bit platforms.
// In order to implement the saturation logic, use a CAS loop.
// From the GCC docs:
// "‘__atomic’ builtins can be used with any integral scalar or pointer type that is 1, 2, 4, or 8 bytes in length"
// From the MSVC docs:
// "_InterlockedCompareExchange64 is available on x86 systems running on any Pentium architecture; it is not
// available on 386 or 486 architectures."
static inline uint64_t
atomic_add_clamped(uint64_t* target, uint64_t amount, uint64_t max)
{
    // In reality, there's virtually no scenario in which this deadlocks.  Just the same, give it some arbitrarily high
    // limit in order to prevent unpredicted deadlocks.  96 is chosen since it's the number of cores on the largest
    // consumer CPU generally used by our customers.
    int attempts = 96;
    while (attempts--) {
        uint64_t old_val = (volatile uint64_t) * target;

        // CAS loop + saturation check
        uint64_t new_val = old_val + amount;
        if (new_val > max || new_val < old_val) {
            return 0;
        }
#if defined(_MSC_VER)
        uint64_t prev_val =
          (uint64_t)InterlockedCompareExchange64((volatile LONG64*)target, (LONG64)new_val, (LONG64)old_val);
        if (prev_val == old_val) {
            return new_val;
        }
#elif defined(__clang__) || defined(__GNUC__)
        if (atomic_compare_exchange_strong_explicit(
              (_Atomic uint64_t*)target, &old_val, new_val, memory_order_seq_cst, memory_order_seq_cst)) {
            return new_val;
        }
#else
#error "Unsupported compiler for atomic operations"
#endif
        // If we reach here, CAS failed; another thread changed `target`
        // Retry until success or until we detect max.
    }

    return 0;
}

// Opaque lock type
typedef struct
{
#ifdef _WIN32
    HANDLE mutex;
#else
    pthread_mutex_t mutex;
#endif
} memlock_t;

// Generic initializer
static inline bool
memlock_init(memlock_t* lock)
{
    if (!lock)
        return false;

#ifdef _WIN32
    lock->mutex = CreateMutex(NULL, FALSE, NULL);
    return lock->mutex != NULL;
#else
    // For POSIX systems, we make sure to use an ERRORCHECK type mutex, since it pushes some of the state checking
    // down to the implementation.
    pthread_mutexattr_t attr;
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_ERRORCHECK);
    return pthread_mutex_init(&lock->mutex, NULL) == 0;
#endif
}

// Unlock function
static inline bool
memlock_unlock(memlock_t* lock)
{
    if (!lock)
        return false;

#ifdef _WIN32
    return ReleaseMutex(lock->mutex);
#else
    return pthread_mutex_unlock(&lock->mutex) == 0;
#endif
}

// trylock function
static inline bool
memlock_trylock(memlock_t* lock)
{
    if (!lock)
        return false;

#ifdef _WIN32
    bool result = WAIT_OBJECT_0 == WaitForSingleObject(lock->mutex, 0); // 0ms timeout -> no wait
#else
    bool result = 0 == pthread_mutex_trylock(&lock->mutex);
#endif
    return result;
}

static inline void
memlock_lock(memlock_t* lock)
{
    if (!lock)
        return;

#ifdef _WIN32
    WaitForSingleObject(lock->mutex, INFINITE);
#else
    pthread_mutex_lock(&lock->mutex);
#endif
}

// Cleanup function
static inline bool
memlock_destroy(memlock_t* lock)
{
    if (!lock)
        return false;

#ifdef _WIN32
    return CloseHandle(lock->mutex);
#else
    return 0 == pthread_mutex_destroy(&lock->mutex);
#endif
}

static inline bool
memalloc_take_guard()
{
    // Ordinarilly, a process-wide semaphore would require a CAS, but since this is thread-local we can just set it.
    if (_MEMALLOC_ON_THREAD)
        return false;
    _MEMALLOC_ON_THREAD = true;
    return true;
}

static inline void
memalloc_yield_guard(void)
{
    // Ideally, we'd actually capture the old state within an object and restore it, but since this is
    // a coarse-grained lock, we just set it to false.
    _MEMALLOC_ON_THREAD = false;
}

#endif
