// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <unicodeobject.h>

#include <cstdint>
#include <string>

#ifndef UNWIND_NATIVE_DISABLE
#include <cxxabi.h>
#define UNW_LOCAL_ONLY
#include <libunwind.h>
#endif  // UNWIND_NATIVE_DISABLE


#include <echion/long.h>
#include <echion/render.h>
#include <echion/vm.h>


// ----------------------------------------------------------------------------
static std::unique_ptr<unsigned char[]> pybytes_to_bytes_and_size(PyObject* bytes_addr,
                                                                  Py_ssize_t* size)
{
    PyBytesObject bytes;

    if (copy_type(bytes_addr, bytes))
        return nullptr;

    *size = bytes.ob_base.ob_size;
    if (*size < 0 || *size > (1 << 20))
        return nullptr;

    auto data = std::make_unique<unsigned char[]>(*size);
    if (copy_generic(reinterpret_cast<char*>(bytes_addr) + offsetof(PyBytesObject, ob_sval),
                     data.get(), *size))
        return nullptr;

    return data;
}

// ----------------------------------------------------------------------------
static Result<std::string> pyunicode_to_utf8(PyObject* str_addr)
{
    PyUnicodeObject str;
    if (copy_type(str_addr, str))
        return ErrorKind::PyUnicodeError;

    PyASCIIObject& ascii = str._base._base;

    if (ascii.state.kind != 1)
        return ErrorKind::PyUnicodeError;

    const char* data = ascii.state.compact
                           ? reinterpret_cast<const char*>(
                                 reinterpret_cast<const uint8_t*>(str_addr) + sizeof(ascii))
                           : static_cast<const char*>(str._base.utf8);
    if (data == NULL)
        return ErrorKind::PyUnicodeError;

    Py_ssize_t size = ascii.state.compact ? ascii.length : str._base.utf8_length;
    if (size < 0 || size > 1024)
        return ErrorKind::PyUnicodeError;

    auto dest = std::string(size, '\0');
    if (copy_generic(data, dest.data(), size))
        return ErrorKind::PyUnicodeError;

    return Result<std::string>(dest);
}

// ----------------------------------------------------------------------------

class StringTable : public std::unordered_map<uintptr_t, std::string>
{
public:
    using Key = uintptr_t;


    static constexpr Key INVALID = 1;
    static constexpr Key UNKNOWN = 2;
    static constexpr Key C_FRAME = 3;

    // Python string object
    [[nodiscard]] inline Result<Key> key(PyObject* s)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        auto k = reinterpret_cast<Key>(s);

        if (this->find(k) == this->end())
        {
#if PY_VERSION_HEX >= 0x030c0000
            // The task name might hold a PyLong for deferred task name formatting.
            std::string str = "Task-";

            auto maybe_long = pylong_to_llong(s);
            if (maybe_long)
            {
                str += std::to_string(*maybe_long);
            }
            else
            {
                auto maybe_unicode = pyunicode_to_utf8(s);
                if (!maybe_unicode)
                {
                    return ErrorKind::PyUnicodeError;
                }

                str = *maybe_unicode;
            }
#else
            auto maybe_unicode = pyunicode_to_utf8(s);
            if (!maybe_unicode)
            {
                return ErrorKind::PyUnicodeError;
            }

            std::string str = std::move(*maybe_unicode);
#endif
            this->emplace(k, str);
            Renderer::get().string(k, str);
        }

        return Result<Key>(k);
    };

    // Python string object
    [[nodiscard]] inline Key key_unsafe(PyObject* s)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        auto k = reinterpret_cast<Key>(s);

        if (this->find(k) == this->end())
        {
#if PY_VERSION_HEX >= 0x030c0000
            // The task name might hold a PyLong for deferred task name formatting.
            auto str = (PyLong_CheckExact(s)) ? "Task-" + std::to_string(PyLong_AsLong(s))
                                              : std::string(PyUnicode_AsUTF8(s));
#else
            auto str = std::string(PyUnicode_AsUTF8(s));
#endif
            this->emplace(k, str);
            Renderer::get().string(k, str);
        }

        return k;
    };

#ifndef UNWIND_NATIVE_DISABLE
    // Native filename by program counter
    [[nodiscard]] inline Key key(unw_word_t pc)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        auto k = static_cast<Key>(pc);

        if (this->find(k) == this->end())
        {
            char buffer[32] = {0};
            std::snprintf(buffer, 32, "native@%p", reinterpret_cast<void*>(k));
            this->emplace(k, buffer);
            Renderer::get().string(k, buffer);
        }

        return k;
    }

    // Native scope name by unwinding cursor
    [[nodiscard]] inline Result<Key> key(unw_cursor_t& cursor)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        unw_proc_info_t pi;
        if ((unw_get_proc_info(&cursor, &pi)))
            return ErrorKind::UnwindError;

        auto k = reinterpret_cast<Key>(pi.start_ip);

        if (this->find(k) == this->end())
        {
            unw_word_t offset;  // Ignored. All the information is in the PC anyway.
            char sym[256];
            if (unw_get_proc_name(&cursor, sym, sizeof(sym), &offset))
                return ErrorKind::UnwindError;

            char* name = sym;

            // Try to demangle C++ names
            char* demangled = NULL;
            if (name[0] == '_' && name[1] == 'Z')
            {
                int status;
                demangled = abi::__cxa_demangle(name, NULL, NULL, &status);
                if (status == 0)
                    name = demangled;
            }

            this->emplace(k, name);
            Renderer::get().string(k, name);

            if (demangled)
                std::free(demangled);
        }

        return Result<Key>(k);
    }
#endif  // UNWIND_NATIVE_DISABLE

    [[nodiscard]] inline Result<std::reference_wrapper<std::string>> lookup(Key key)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        auto it = this->find(key);
        if (it == this->end())
            return ErrorKind::LookupError;

        return std::ref(it->second);
    };

    StringTable() : std::unordered_map<uintptr_t, std::string>()
    {
        this->emplace(0, "");
        this->emplace(INVALID, "<invalid>");
        this->emplace(UNKNOWN, "<unknown>");
    };

private:
    std::mutex table_lock;
};

// We make this a reference to a heap-allocated object so that we can avoid
// the destruction on exit. We are in charge of cleaning up the object. Note
// that the object will leak, but this is not a problem.
inline StringTable& string_table = *(new StringTable());
