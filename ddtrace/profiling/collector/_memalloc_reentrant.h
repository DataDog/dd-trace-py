#ifndef _DDTRACE_MEMALLOC_REENTRANT_H
#define _DDTRACE_MEMALLOC_REENTRANT_H

#ifdef _WIN32
#include <windows.h>
#else
#define _POSIX_C_SOURCE 200809L
#include <time.h>
#include <stdatomic.h>
#include <errno.h>
#include <pthread.h>
#include <unistd.h>
#endif
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

// This is a simple thread-local reentrance guard.
// Note the use of dynamic TLS.  Since this is a dynamic library it isn't in control of how much static TLS is
// available, since that is determined at load-time by the main executable.  We've already had issues with popping
// some users' static TLS limits, so use dynamic TLS for now.

// Cross-platform macro for defining thread-local storage
// NB - we use dynamic-global on Linux because the others are problematic
#if defined(_MSC_VER) // Check for MSVC compiler
#define MEMALLOC_TLS __declspec(thread)
#elif defined(__GNUC__) || defined(__clang__) // GCC or Clang
#define MEMALLOC_TLS __attribute__((tls_model("global-dynamic"))) __thread
#else
#error "Unsupported compiler for thread-local storage"
#endif
extern bool _MEMALLOC_ON_THREAD;

// This is a saturating atomic increment for 32- and 64-bit platforms.
// In order to implement the saturation logic, use a CAS loop.
// From the GCC docs:
// "‘__atomic’ builtins can be used with any integral scalar or pointer type that is 1, 2, 4, or 8 bytes in length"
// From the MSVC docs:
// "_InterlockedCompareExchange64 is available on x86 systems running on any Pentium architecture; it is not
// available on 386 or 486 architectures."
static inline uint64_t
atomic_inc_clamped(uint64_t* target, uint64_t max)
{
    // In reality, there's virtually no scenario in which this deadlocks.  Just the same, give it some arbitrarily high
    // limit in order to prevent unpredicted deadlocks.  96 is chosen since it's the number of cores on the largest
    // consumer CPU generally used by our customers.
    int attempts = 96;
    while (attempts--) {
        uint64_t old_val = (volatile uint64_t) * target;
        uint64_t new_val;

        // Saturation check
        if (old_val == UINT64_MAX) {
            return 0;
        }

        // CAS loop
        new_val = old_val + 1;
#if defined(_MSC_VER)
        uint64_t prev_val =
          (uint64_t)InterlockedCompareExchange64((volatile LONG*)target, (LONG)new_val, (LONG)old_val);
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

// Timed lock function
static inline bool
memlock_lock_timed(memlock_t* lock, uint32_t timeout_ms)
{
    if (!lock)
        return false;

#ifdef __linux__
    // On Linux, we need to make sure we didn't just fork
    // pthreads will guarantee the lock is consistent, but we at least need to clear it
    static pid_t my_pid = 0;
    if (my_pid == 0) {
        my_pid = getpid();
    } else if (my_pid != getpid()) {
        // We've forked, so we need to free the lock
        memlock_unlock(lock);
        my_pid = getpid();
    }
#endif

#ifdef _WIN32
    DWORD result = WaitForSingleObject(lock->mutex, timeout_ms);
    return result == WAIT_OBJECT_0;
#elif defined(__APPLE__)
    // `pthread_mutex_timedlock` isn't available on macOS, so we implement a hot loop
    struct timespec start_time, current_time;
    clock_gettime(CLOCK_REALTIME, &start_time); // Get the starting time
                                                //
    while (true) {
        if (pthread_mutex_trylock(&lock->mutex) == 0) {
            return true;
        }

        // Else, gotta check the time
        clock_gettime(CLOCK_REALTIME, &current_time);
        uint32_t elapsed_ms =
          (current_time.tv_sec - start_time.tv_sec) * 1e3 + (current_time.tv_nsec - start_time.tv_nsec) / 1e6;

        if (elapsed_ms >= timeout_ms) {
            return false; // Timeout
        }

        // Sleep for a tick
        struct timespec ts = { 0, 4e6 }; // 4ms
        nanosleep(&ts, NULL);
    }
#else
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    ts.tv_sec += timeout_ms / 1e3;
    ts.tv_nsec += (timeout_ms % (unsigned int)1e3) * 1e6;
    ts.tv_sec += ts.tv_nsec / 1e9;
    ts.tv_nsec %= (unsigned int)1e9;

    int result = pthread_mutex_timedlock(&lock->mutex, &ts);
    return result == 0;
#endif

    // We should never get here, since each platform should return from its block, but we add it to silence static
    // analysis warnings
    return false;
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
    return pthread_mutex_destroy(&lock->mutex) == 0;
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
