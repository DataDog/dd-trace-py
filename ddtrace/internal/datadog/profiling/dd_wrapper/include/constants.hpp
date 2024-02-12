#pragma once

// Default value for the max frames; this number will always be overridden by whatever the default
// is for ddtrace/settings/profiling.py:ProfilingConfig.max_frames, but should conform
constexpr unsigned int g_default_max_nframes = 64;

// Maximum number of frames admissible in the Profiling backend.  If a user exceeds this number, then
// their stacks may be silently truncated, which is unfortunate.
constexpr unsigned int g_backend_max_nframes = 512;
