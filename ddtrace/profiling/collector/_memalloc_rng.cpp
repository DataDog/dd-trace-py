#include "_memalloc_rng.h"

#include <cstdint>
#include <cstdlib>
#include <ctime>
#include <mutex>
#include <pthread.h>
#include <thread>
#include <time.h>
#include <unistd.h>

// Fork-safe fast PRNG using xorshift* algorithm
// Lock-free (no mutexes, avoids rand() deadlock risk) and fork-safe via pthread_atfork
// Similar to ddtrace/internal/_rand.pyx but optimized for C++
static uint64_t fast_rng_state = 0;
static std::once_flag memalloc_rng_debug_seed_once_flag;
static uint64_t debug_seed_value = 0;

void
memalloc_rng_reseed()
{
    // If the debug seed is set, use it to seed the RNG instead of using
    // entropy-based seeding, but we only do this once per process.
    std::call_once(memalloc_rng_debug_seed_once_flag, []() {
        char* val = getenv("_DD_MEMALLOC_DEBUG_RNG_SEED");
        if (val) {
            debug_seed_value = static_cast<uint64_t>(atoi(val));
            if (debug_seed_value == 0) {
                debug_seed_value = 1;
            }
        }
    });

    if (debug_seed_value != 0) {
        fast_rng_state = debug_seed_value;
        return;
    }

    // Called automatically in child process after fork via pthread_atfork
    // Only called once per fork, not on every random number generation
    // Uses async-signal-safe functions only (clock_gettime, getpid)
    // Note: We assume clock_gettime(CLOCK_MONOTONIC) always succeeds. In practice,
    // CLOCK_MONOTONIC is always available on POSIX systems and failures are extremely rare.
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    uint64_t nanos = static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL + static_cast<uint64_t>(ts.tv_nsec);
    // Combine multiple sources of entropy: PID, time, and address of static variable (ASLR)
    fast_rng_state = static_cast<uint64_t>(getpid()) ^ nanos ^ reinterpret_cast<uintptr_t>(&fast_rng_state);
    // Ensure state is non-zero (xorshift requires non-zero state)
    if (fast_rng_state == 0) {
        fast_rng_state = 1;
    }
}

double
memalloc_rng_uniform()
{
    // Lazy initialization on first call (only happens once)
    if (fast_rng_state == 0) {
        memalloc_rng_reseed();
    }

    // Lock-free xorshift* operations - zero syscall overhead, no mutexes
    // Same algorithm as ddtrace/internal/_rand.pyx
    fast_rng_state ^= fast_rng_state >> 21;
    fast_rng_state ^= fast_rng_state << 35;
    fast_rng_state ^= fast_rng_state >> 4;
    fast_rng_state *= 2685821657736338717ULL;

    // Convert to [0, 1) range
    return static_cast<double>(fast_rng_state) / static_cast<double>(UINT64_MAX + 1.0);
}
