// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

// CPU Time mode
inline int cpu = 0;

// For cpu time mode, Echion only unwinds threads that're running by default.
// Set this to false to unwind all threads.
inline bool ignore_non_running_threads = true;

// Maximum number of frames to unwind
inline unsigned int max_frames = 2048;

// ----------------------------------------------------------------------------
inline void
_set_cpu(int new_cpu)
{
    cpu = new_cpu;
}

// ----------------------------------------------------------------------------
inline void
_set_ignore_non_running_threads(bool new_ignore_non_running_threads)
{
    ignore_non_running_threads = new_ignore_non_running_threads;
}
