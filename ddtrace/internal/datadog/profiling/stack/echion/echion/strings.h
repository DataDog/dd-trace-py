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
#include <echion/vm.h>

constexpr ssize_t MAX_STRING_SIZE = 1 << 20; // 1 MiB

// ----------------------------------------------------------------------------
std::unique_ptr<unsigned char[]>
pybytes_to_bytes_and_size(PyObject* bytes_addr, Py_ssize_t* size);

// ----------------------------------------------------------------------------
Result<std::string>
pyunicode_to_utf8(PyObject* str_addr);

// ----------------------------------------------------------------------------
// NOTE: StringTag is used to salt the upper bits of string table keys.
// This prevents collisions when Python reuses memory addresses for different
// types of strings (e.g., a Task name getting deallocated and the same address
// being reused for a code object's name). Without tags, we'd return stale
// cached values for the wrong string type.
enum class StringTag : uint8_t
{
    Unknown = 0,     // Default/untagged (for backwards compatibility)
    FileName = 1,    // co_filename from code objects
    FuncName = 2,    // co_name / co_qualname from code objects
    TaskName = 3,    // asyncio Task names
    GreenletName = 4 // greenlet names
};

class StringTable : public std::unordered_map<uintptr_t, std::string>
{
  public:
    using Key = uintptr_t;

    // Tag is stored in the upper 8 bits of the key (bits 56-63).
    // On x86_64, only 48 bits are used for virtual addresses, so this is safe.
    // On other architectures with larger address spaces, collisions are still
    // unlikely and the worst case is just a cache miss (re-read the string).
    static constexpr int TAG_SHIFT = 56;
    static constexpr Key TAG_MASK = static_cast<Key>(0xFF) << TAG_SHIFT;

    [[nodiscard]] static constexpr Key make_tagged_key(uintptr_t addr, StringTag tag)
    {
        return (addr & ~TAG_MASK) | (static_cast<Key>(tag) << TAG_SHIFT);
    }

    static constexpr Key INVALID = 1;
    static constexpr Key UNKNOWN = 2;
    static constexpr Key C_FRAME = 3;

    // Python string object
    [[nodiscard]] Result<Key> key(PyObject* s, StringTag tag = StringTag::Unknown);

    [[nodiscard]] Key key_unsafe(PyObject* s, StringTag tag = StringTag::Unknown);

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

    void postfork_child()
    {
        // NB placement-new to re-init and leak the mutex because doing anything else is UB
        new (&table_lock) std::mutex();
    }

  private:
    mutable std::mutex table_lock;
};
