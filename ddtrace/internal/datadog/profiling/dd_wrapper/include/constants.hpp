#pragma once

#include <cstdint>
#include <string_view>

// Default value for the max frames; this number will always be overridden by whatever the default
// is for ddtrace/settings/profiling.py:ProfilingConfig.max_frames, but should conform
constexpr unsigned int g_default_max_nframes = 64;

// Maximum number of frames admissible in the Profiling backend.  If a user exceeds this number, then
// their stacks may be silently truncated, which is unfortunate.
constexpr unsigned int g_backend_max_nframes = 512;

// Maximum amount of time, in seconds, to wait for crashtracker send operations
constexpr uint64_t g_crashtracker_timeout_secs = 5;

// The name of the programming language this supports
constexpr std::string_view g_family = "python";

// The name of the programming language runtime this supports
constexpr std::string_view g_runtime = "cpython";
