#pragma once

#include "libdatadog_helpers.hpp"
#include "profiler_stats.hpp"

#include <mutex>

namespace Datadog {

// RAII guard for the active profile and its stats under profile_mtx.
// Movable via unique_lock; non-copyable.
struct ProfileBorrow
{
    std::unique_lock<std::mutex> lock;
    ddprof::Profile& profile;
    ProfilerStats& stats;
};

} // namespace Datadog
