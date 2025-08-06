#pragma once

#include <cstdint>
#include <string_view>

// Default value for the max frames; this number will always be overridden by whatever the default
// is for ddtrace/settings/profiling.py:ProfilingConfig.max_frames, but should conform
constexpr unsigned int g_default_max_nframes = 64;

// Maximum number of frames admissible in the Profiling backend.  If a user exceeds this number, then
// their stacks may be silently truncated, which is unfortunate.
constexpr unsigned int g_backend_max_nframes = 512;

// Default value for the max number of samples to keep in the StaticSamplePool
constexpr size_t g_default_sample_pool_capacity = 4;

// Default name of the runtime.  This will almost certainly get overridden by the caller, but we set it here
// as a reasonable default just in case.
constexpr std::string_view g_runtime_name = "CPython";

// Name of the language we support
constexpr std::string_view g_language_name = "python";

// Name of the library
constexpr std::string_view g_library_name = "dd-trace-py";
