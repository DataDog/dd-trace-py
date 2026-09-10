// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

// Hard safety bound for physical stack discovery. Reportable sync and task
// frame materialization is separately bounded by DD_PROFILING_MAX_FRAMES.
inline constexpr unsigned int MAX_STACK_DISCOVERY_DEPTH = 2048;
