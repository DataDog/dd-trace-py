// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#define Py_BUILD_CORE
#include <Python.h>

#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#if PY_VERSION_HEX >= 0x030c0000
// https://github.com/python/cpython/issues/108216#issuecomment-1696565797
#undef _PyGC_FINALIZED
#endif
#include <frameobject.h>
#if PY_VERSION_HEX >= 0x030e0000
#include <internal/pycore_interpframe_structs.h>
#elif PY_VERSION_HEX >= 0x030b0000
#include <internal/pycore_frame.h>
#endif

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <functional>

#include <echion/cache.h>
#if PY_VERSION_HEX >= 0x030b0000
#include <echion/stack_chunk.h>
#endif // PY_VERSION_HEX >= 0x030b0000
#include <echion/strings.h>
#include <echion/vm.h>

// Forward declaration
class EchionSampler;

// ----------------------------------------------------------------------------
class Frame
{
  public:
    using Ref = std::reference_wrapper<Frame>;
    using Ptr = std::unique_ptr<Frame>;
    using Key = uintptr_t;

    // ------------------------------------------------------------------------
    Key cache_key = 0;
    StringTable::Key filename = 0;
    StringTable::Key name = 0;

    struct _location
    {
        unsigned line = 0;
        unsigned line_end = 0;
        unsigned column = 0;
        unsigned column_end = 0;
    } location;

#if PY_VERSION_HEX >= 0x030b0000
    bool is_entry = false;
#endif

    // ------------------------------------------------------------------------
    Frame(StringTable::Key filename, StringTable::Key name)
      : filename(filename)
      , name(name)
    {
    }
    Frame(StringTable::Key name)
      : name(name) {};
    [[nodiscard]] static Result<Frame::Ptr> create(EchionSampler& echion, PyCodeObject* code, int lasti);

#if PY_VERSION_HEX >= 0x030b0000
    [[nodiscard]] static Result<std::reference_wrapper<Frame>> read(EchionSampler& echion,
                                                                    _PyInterpreterFrame* frame_addr,
                                                                    _PyInterpreterFrame** prev_addr);
#else
    [[nodiscard]] static Result<std::reference_wrapper<Frame>> read(EchionSampler& echion,
                                                                    PyObject* frame_addr,
                                                                    PyObject** prev_addr);
#endif

    [[nodiscard]] static Result<std::reference_wrapper<Frame>> get(EchionSampler& echion,
                                                                   PyCodeObject* code_addr,
                                                                   int lasti);
    static Frame& get(EchionSampler& echion, StringTable::Key name);

  private:
    [[nodiscard]] Result<void> inline infer_location(PyCodeObject* code, int lasti);
    // co_firstlineno is included in the key to prevent stale cache hits when Python
    // reuses a freed PyCodeObject's memory address for a new code object. Without it,
    // the profiler can return a cached <module> frame for a function frame.
    static inline Key key(PyCodeObject* code, int lasti, int firstlineno);
};

inline auto INVALID_FRAME = Frame(StringTable::INVALID);
inline auto UNKNOWN_FRAME = Frame(StringTable::UNKNOWN);
inline auto C_FRAME = Frame(StringTable::C_FRAME);
