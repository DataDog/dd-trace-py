#pragma once

#include "dd_wrapper/include/constants.hpp"

// Default sampling frequency in microseconds.  This will almost certainly be overridden by dynamic sampling.
constexpr unsigned int g_default_sampling_period_us = 10000; // 100 Hz
constexpr unsigned int g_min_sampling_period_us = g_default_sampling_period_us / 2;
constexpr unsigned int g_max_sampling_period_us = 3000000; // 3 seconds
constexpr double g_default_sampling_period_s = g_default_sampling_period_us / 1e6;

// Echion maintains a cache of frames--the size of this cache is specified up-front.
constexpr unsigned int g_default_echion_frame_cache_size = 1024;
