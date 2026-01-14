// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define Py_BUILD_CORE
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <unicodeobject.h>

#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>

#include <echion/long.h>
#include <echion/render.h>
#include <echion/vm.h>

constexpr ssize_t MAX_STRING_SIZE = 1 << 20; // 1 MiB

// ----------------------------------------------------------------------------
std::unique_ptr<unsigned char[]>
pybytes_to_bytes_and_size(PyObject* bytes_addr, Py_ssize_t* size);

// ----------------------------------------------------------------------------
Result<std::string>
pyunicode_to_utf8(PyObject* str_addr);

// ----------------------------------------------------------------------------

class StringTable : public std::unordered_map<uintptr_t, std::string>
{
  public:
    using Key = uintptr_t;

    static constexpr Key INVALID = 1;
    static constexpr Key UNKNOWN = 2;
    static constexpr Key C_FRAME = 3;

    // Python string object
    [[nodiscard]] Result<Key> key(PyObject* s);

    // Python string object
    [[nodiscard]] Key key_unsafe(PyObject* s);

    [[nodiscard]] Result<std::reference_wrapper<const std::string>> lookup(Key key) const;

    [[nodiscard]] inline size_t size() const
    {
        const std::lock_guard<std::mutex> lock(table_lock);
        return std::unordered_map<uintptr_t, std::string>::size();
    };

    StringTable()
      : std::unordered_map<uintptr_t, std::string>()
    {
        this->emplace(0, "");
        this->emplace(INVALID, "<invalid>");
        this->emplace(UNKNOWN, "<unknown>");
    };

  private:
    mutable std::mutex table_lock;
};

// We make this a reference to a heap-allocated object so that we can avoid
// the destruction on exit. We are in charge of cleaning up the object. Note
// that the object will leak, but this is not a problem.
inline StringTable& string_table = *(new StringTable());
