// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#if defined __arm__
using mojo_int_t = long;
using mojo_uint_t = unsigned long;
using mojo_ref_t = unsigned long;
#else
using mojo_int_t = long long;
using mojo_uint_t = unsigned long long;
using mojo_ref_t = unsigned long long;
#endif
