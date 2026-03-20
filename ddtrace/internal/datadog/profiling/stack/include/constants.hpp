#pragma once

#include <cstddef>

#include "dd_wrapper/include/constants.hpp"

// Default sampling frequency in microseconds.  This will almost certainly be overridden by dynamic sampling.
constexpr unsigned int g_default_sampling_period_us = 10000;     // 100 Hz
constexpr unsigned int g_min_sampling_period_us = 100;           // 10000 Hz
constexpr unsigned int g_max_sampling_period_us = 100000;        // 100 ms
constexpr unsigned int g_adaptive_sampling_interval_us = 250000; // 250 ms
constexpr double g_default_sampling_period_s = g_default_sampling_period_us / 1e6;
constexpr double g_target_overhead = 0.01; // 1% overhead

// Maximum number of threads to sample per cycle for thread sub-sampling.
// Threads are cycled round-robin so all threads are covered over time.
constexpr size_t g_default_max_threads_per_cycle = 8;

// Echion maintains a cache of frames--the size of this cache is specified up-front.
constexpr unsigned int g_default_echion_frame_cache_size = 1024;
