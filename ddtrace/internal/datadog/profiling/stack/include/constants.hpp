#pragma once

#include "dd_wrapper/include/constants.hpp"

// Default sampling frequency in microseconds.  This will almost certainly be overridden by dynamic sampling.
constexpr unsigned int g_default_sampling_period_us = 10000;     // 100 Hz
constexpr unsigned int g_min_sampling_period_us = 100;           // 10000 Hz
constexpr unsigned int g_max_sampling_period_us = 1000000;       // 1 seconds
constexpr unsigned int g_adaptive_sampling_interval_us = 250000; // 250 ms
constexpr double g_default_sampling_period_s = g_default_sampling_period_us / 1e6;
constexpr double g_target_overhead = 0.01; // 1% overhead

// Maximum number of threads to sample per cycle. When the number of threads exceeds this,
// reservoir sampling (Algorithm R) is used to select a uniform random subset.
// 0 means no limit (sample all threads).
constexpr unsigned int g_default_max_threads_per_sample = 25;

// Echion retains recently rendered frames across stack walks. Scale the cache with
// the configured stack depth while bounding both churn and retained memory.
constexpr unsigned int g_min_echion_frame_cache_size = 256;
constexpr unsigned int g_max_echion_frame_cache_size = 1024;
constexpr unsigned int g_echion_frame_cache_size_multiplier = 4;
