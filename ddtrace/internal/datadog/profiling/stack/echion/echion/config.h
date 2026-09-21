// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

// Internal safety ceiling for stack unwinding and task-aware stitching,
// separate from the configured per-sample frame limit.
inline constexpr unsigned int MAX_STACK_UNWIND_SAFETY_LIMIT = 2048;
