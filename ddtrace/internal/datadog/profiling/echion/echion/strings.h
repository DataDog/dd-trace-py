// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <unicodeobject.h>

#include <cstdint>
#include <exception>
#include <string>

#ifndef UNWIND_NATIVE_DISABLE
#include <cxxabi.h>
#define UNW_LOCAL_ONLY
#include <libunwind.h>
#endif  // UNWIND_NATIVE_DISABLE


#include <echion/long.h>
#include <echion/render.h>
#include <echion/vm.h>

class StringError : public std::exception
{
    const char* what() const noexcept override
    {
        return "StringError";
    }
};

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
    if (copy_generic(((char*)bytes_addr) + offsetof(PyBytesObject, ob_sval), data.get(), *size))
        return nullptr;

    return data;
}

// ----------------------------------------------------------------------------
static std::string pyunicode_to_utf8(PyObject* str_addr)
{
    PyUnicodeObject str;
    if (copy_type(str_addr, str))
        throw StringError();

    PyASCIIObject& ascii = str._base._base;

    if (ascii.state.kind != 1)
        throw StringError();

    const char* data = ascii.state.compact ? (const char*)(((uint8_t*)str_addr) + sizeof(ascii))
                                           : (const char*)str._base.utf8;
    if (data == NULL)
        throw StringError();

    Py_ssize_t size = ascii.state.compact ? ascii.length : str._base.utf8_length;
    if (size < 0 || size > 1024)
        throw StringError();

    auto dest = std::string(size, '\0');
    if (copy_generic(data, dest.data(), size))
        throw StringError();

    return dest;
}

// ----------------------------------------------------------------------------

class StringTable : public std::unordered_map<uintptr_t, std::string>
{
public:
    using Key = uintptr_t;

    class Error : public std::exception
    {
    };

    class LookupError : public Error
    {
    };

    static constexpr Key INVALID = 1;
    static constexpr Key UNKNOWN = 2;

    // Python string object
    inline Key key(PyObject* s)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        auto k = (Key)s;

        if (this->find(k) == this->end())
        {
            try
            {
#if PY_VERSION_HEX >= 0x030c0000
                // The task name might hold a PyLong for deferred task name formatting.
                std::string str = "Task-";
                try
                {
                    str += std::to_string(pylong_to_llong(s));
                }
                catch (LongError&)
                {
                    str = pyunicode_to_utf8(s);
                }
#else
                auto str = pyunicode_to_utf8(s);
#endif
                this->emplace(k, str);
                Renderer::get().string(k, str);
            }
            catch (StringError&)
            {
                throw Error();
            }
        }

        return k;
    };

    // Python string object
    inline Key key_unsafe(PyObject* s)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        auto k = (Key)s;

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
    inline Key key(unw_word_t pc)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        auto k = (Key)pc;

        if (this->find(k) == this->end())
        {
            char buffer[32] = {0};
            std::snprintf(buffer, 32, "native@%p", (void*)k);
            this->emplace(k, buffer);
            Renderer::get().string(k, buffer);
        }

        return k;
    }

    // Native scope name by unwinding cursor
    inline Key key(unw_cursor_t& cursor)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        unw_proc_info_t pi;
        if ((unw_get_proc_info(&cursor, &pi)))
            throw Error();

        auto k = (Key)pi.start_ip;

        if (this->find(k) == this->end())
        {
            unw_word_t offset;  // Ignored. All the information is in the PC anyway.
            char sym[256];
            if (unw_get_proc_name(&cursor, sym, sizeof(sym), &offset))
                throw Error();

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

        return k;
    }
#endif  // UNWIND_NATIVE_DISABLE

    inline std::string& lookup(Key key)
    {
        const std::lock_guard<std::mutex> lock(table_lock);

        auto it = this->find(key);
        if (it == this->end())
            throw LookupError();

        return it->second;
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
