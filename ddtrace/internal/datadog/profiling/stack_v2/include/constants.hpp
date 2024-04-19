#include "dd_wrapper/include/constants.hpp"

// Default sampling frequency in microseconds.  This will almost certainly be overridden by dynamic sampling.
constexpr unsigned int g_default_sampling_period_us = 10000; // 100 Hz
constexpr double g_default_sampling_period_s = g_default_sampling_period_us / 1e6;

// Echion maintains a cache of frames--the size of this cache is specified up-front.
constexpr unsigned int g_default_echion_frame_cache_size = 1024;
