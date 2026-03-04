// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

typedef unsigned long microsecond_t;

#define TS_TO_MICROSECOND(ts) ((ts).tv_sec * 1e6 + (ts).tv_nsec / 1e3)
#define TV_TO_MICROSECOND(tv) ((tv).seconds * 1e6 + (tv).microseconds)
