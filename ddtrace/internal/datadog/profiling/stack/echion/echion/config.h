// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

// Safety limit for Python frames collected during stack unwinding and task-aware stitching.
inline constexpr unsigned int MAX_STACK_DISCOVERY_DEPTH = 2048;
