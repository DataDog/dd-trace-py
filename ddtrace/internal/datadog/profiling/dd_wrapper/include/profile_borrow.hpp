#pragma once

#include "libdatadog_helpers.hpp"
#include "profiler_stats.hpp"

#include <mutex>

namespace Datadog {

// RAII wrapper for borrowing both profile and stats under profile_mtx.
// Two fields instead of one, so this doesn't use Borrow<T>.
// Movable via unique_lock; non-copyable by default.
struct ProfileBorrow
{
    std::unique_lock<std::mutex> lock;
    ddprof::Profile& profile;
    ProfilerStats& stats;
};

} // namespace Datadog
