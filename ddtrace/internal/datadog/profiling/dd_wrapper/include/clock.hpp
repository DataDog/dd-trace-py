#pragma once

#include <cstdint>
#include <time.h>

#ifdef __APPLE__
#include <mach/mach_time.h>
#endif

namespace Datadog {

// Returns the current monotonic time in nanoseconds using the same clock as
// Python's `time.monotonic_ns`:
//   - Linux:  `clock_gettime`
//   - macOS:  `mach_absolute_time`, which excludes sleep time
//
// These must agree because `push_monotonic_ns` computes a static offset between
// this clock and the wall epoch, then applies it to values passed in by Python.
// Using `clock_gettime` on macOS would be wrong: it maps to
// `mach_continuous_time`, which includes sleep time and diverges from
// `mach_absolute_time` after system sleep.
inline int64_t
get_monotonic_ns()
{
#ifdef __APPLE__
    mach_timebase_info_data_t timebase;
    mach_timebase_info(&timebase);
    return static_cast<int64_t>(static_cast<__uint128_t>(mach_absolute_time()) * timebase.numer / timebase.denom);
#else
    timespec ts{ 0, 0 };
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return static_cast<int64_t>(ts.tv_sec) * 1'000'000'000LL + ts.tv_nsec;
#endif
}

} // namespace Datadog
