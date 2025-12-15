// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
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
#define Py_BUILD_CORE
#include <internal/pycore_interpframe_structs.h>
#elif PY_VERSION_HEX >= 0x030b0000
#define Py_BUILD_CORE
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
        int line = 0;
        int line_end = 0;
        int column = 0;
        int column_end = 0;
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
    [[nodiscard]] static Result<Frame::Ptr> create(PyCodeObject* code, int lasti);

#if PY_VERSION_HEX >= 0x030b0000
    [[nodiscard]] static Result<std::reference_wrapper<Frame>> read(_PyInterpreterFrame* frame_addr,
                                                                    _PyInterpreterFrame** prev_addr);
#else
    [[nodiscard]] static Result<std::reference_wrapper<Frame>> read(PyObject* frame_addr, PyObject** prev_addr);
#endif

    [[nodiscard]] static Result<std::reference_wrapper<Frame>> get(PyCodeObject* code_addr, int lasti);
    static Frame& get(StringTable::Key name);

  private:
    [[nodiscard]] Result<void> inline infer_location(PyCodeObject* code, int lasti);
    static inline Key key(PyCodeObject* code, int lasti);
};

inline auto INVALID_FRAME = Frame(StringTable::INVALID);
inline auto UNKNOWN_FRAME = Frame(StringTable::UNKNOWN);
inline auto C_FRAME = Frame(StringTable::C_FRAME);

// We make this a raw pointer to prevent its destruction on exit, since we
// control the lifetime of the cache.
inline LRUCache<uintptr_t, Frame>* frame_cache = nullptr;
void
init_frame_cache(size_t capacity);
void
reset_frame_cache();
