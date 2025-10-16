// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define MOJO_VERSION 3

enum MojoEvent
{
    MOJO_RESERVED,
    MOJO_METADATA,
    MOJO_STACK,
    MOJO_FRAME,
    MOJO_FRAME_INVALID,
    MOJO_FRAME_REF,
    MOJO_FRAME_KERNEL,
    MOJO_GC,
    MOJO_IDLE,
    MOJO_METRIC_TIME,
    MOJO_METRIC_MEMORY,
    MOJO_STRING,
    MOJO_STRING_REF,
    MOJO_MAX,
};

#if defined __arm__
using mojo_int_t = long;
using mojo_uint_t = unsigned long;
using mojo_ref_t = unsigned long;
#else
using mojo_int_t = long long;
using mojo_uint_t = unsigned long long;
using mojo_ref_t = unsigned long long;
#endif
