#pragma once

#include "dd_wrapper/include/constants.hpp"

// Default sampling frequency in microseconds.  This will almost certainly be overridden by dynamic sampling.
constexpr unsigned int g_default_sampling_period_us = 10000; // 100 Hz
constexpr unsigned int g_min_sampling_period_us = g_default_sampling_period_us / 2;
constexpr unsigned int g_max_sampling_period_us = 1000000;       // 1 seconds
constexpr unsigned int g_adaptive_sampling_interval_us = 250000; // 250 ms
constexpr double g_default_sampling_period_s = g_default_sampling_period_us / 1e6;
constexpr double g_target_overhead = 0.01; // 1% overhead

// Echion maintains a cache of frames--the size of this cache is specified up-front.
constexpr unsigned int g_default_echion_frame_cache_size = 1024;
