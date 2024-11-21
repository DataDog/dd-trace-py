#ifndef _DDTRACE_MEMALLOC_REENTRANT_H
#define _DDTRACE_MEMALLOC_REENTRANT_H

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <errno.h>
#include <pthread.h>
#endif

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
extern MEMALLOC_TLS bool _MEMALLOC_ON_THREAD;

// Simple CAS for bools
#if defined(_MSC_VER)
static inline bool
cas_thread_local_bool(bool* target, bool expected, bool desired)
{
    LONG expected_long = (LONG)expected;
    LONG desired_long = (LONG)desired;
    return (LONG)expected == InterlockedCompareExchange((volatile LONG*)target, desired_long, expected_long);
}
#elif defined(__GNUC__) || defined(__clang__) // GCC or Clang
static inline bool
cas_thread_local_bool(bool* target, bool expected, bool desired)
{
    return __atomic_compare_exchange_n(target, &expected, &desired, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}
#else
#error "Unsupported compiler for atomic operations"
#endif

// This is a simple clamped atomic increment
// On 64-bit systems, do thigns the normal way
// On 32-bit systems, do a CAS loop on the low word; this is an `inc` and not an `add`, so this word must mutate.
static inline uint64_t
atomic_inc_clamped(uint64_t* target, uint64_t max)
{
    uint64_t old_val = *target;
    // Counters will _never_ return 0 after an increment, so the caller knows this is capped.
    if (old_val > max)
        return 0;

    uint64_t new_val;

#if UINTPTR_MAX == UINT64_MAX && defined(_MSC_VER)
    // 64-bit MSVC
    new_val = _InterlockedIncrement64((volatile long long*)target);
#elif UINTPTR_MAX == UINT64_MAX && (defined(__GNUC__) || defined(__clang__))
    // 64-bit gcc/clang
    new_val = __atomic_add_fetch(target, 1, __ATOMIC_SEQ_CST);
#elif defined(_MSC_VER)
    // 32-bit MSVC must support uint64_t values, but can't do atomic operations on them.
    new_val = old_val + 1;
    while (InterlockedCompareExchange64((volatile LONGLONG*)target, new_val, old_val) != old_val) {
        // Do nothing, just try again.  This will terminate eventually.
    }
#elif defined(__GNUC__) || defined(__clang__)
    // 32-bit gcc/clang is in a similar boat as 32-bit Windows.
    new_val = old_val + 1;
    while (!__atomic_compare_exchange_n(target, &old_val, &new_val, false, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) {
        // Do nothing, just try again.  This will terminate eventually.
    }
#else
#error "Unsupported compiler for atomic operations"
#endif

    return new_val;
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

// Timed lock function
static inline bool
memlock_timed(memlock_t* lock, uint32_t timeout_ms)
{
#ifdef __linux__
    // On Linux, we need to make sure we didn't just fork
    // pthreads will guarantee the lock is consistent, but we at least need to clear it
    static pid_t my_pid = getpid();
    if (!lock)
        return false;

    if (my_pid != getpid()) {
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

    // We should never get here, since each platform should return from its block
    assert(false);
    return false;
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
