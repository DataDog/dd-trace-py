// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#if defined PL_LINUX
#include <time.h>
#elif defined PL_DARWIN
#include <mach/clock.h>
#include <mach/mach.h>

inline clock_serv_t cclock;
#endif

typedef unsigned long microsecond_t;

inline microsecond_t last_time = 0;

#define TS_TO_MICROSECOND(ts) ((ts).tv_sec * 1e6 + (ts).tv_nsec / 1e3)
#define TV_TO_MICROSECOND(tv) ((tv).seconds * 1e6 + (tv).microseconds)

// ----------------------------------------------------------------------------
static microsecond_t
gettime()
{
#if defined PL_LINUX
    struct timespec ts;
    if (clock_gettime(CLOCK_BOOTTIME, &ts))
        return 0;
    return TS_TO_MICROSECOND(ts);
#elif defined PL_DARWIN
    mach_timespec_t ts;
    clock_get_time(cclock, &ts);
    return TS_TO_MICROSECOND(ts);
#endif
}
