#include <echion/strings.h>

std::unique_ptr<unsigned char[]>
pybytes_to_bytes_and_size(PyObject* bytes_addr, Py_ssize_t* size)
{
    PyBytesObject bytes;

    if (copy_type(bytes_addr, bytes))
        return nullptr;

    *size = bytes.ob_base.ob_size;
    if (*size < 0 || *size > MAX_STRING_SIZE)
        return nullptr;

    auto data = std::make_unique<unsigned char[]>(*size);
    if (copy_generic(reinterpret_cast<char*>(bytes_addr) + offsetof(PyBytesObject, ob_sval), data.get(), *size))
        return nullptr;

    return data;
}
Result<std::string>
pyunicode_to_utf8(PyObject* str_addr)
{
    PyUnicodeObject str;
    if (copy_type(str_addr, str))
        return ErrorKind::PyUnicodeError;

    PyASCIIObject& ascii = str._base._base;

    if (ascii.state.kind != 1)
        return ErrorKind::PyUnicodeError;

    const char* data = ascii.state.compact
                         ? reinterpret_cast<const char*>(reinterpret_cast<const uint8_t*>(str_addr) + sizeof(ascii))
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

[[nodiscard]] Result<StringTable::Key>
StringTable::key(PyObject* s, StringTag tag)
{
    const std::lock_guard<std::mutex> lock(table_lock);

    auto k = make_tagged_key(reinterpret_cast<uintptr_t>(s), tag);

    if (this->find(k) == this->end()) {
#if PY_VERSION_HEX >= 0x030c0000
        // The task name might hold a PyLong for deferred task name formatting.
        std::string str = "Task-";

        auto maybe_long = pylong_to_llong(s);
        if (maybe_long) {
            str += std::to_string(*maybe_long);
        } else {
            auto maybe_unicode = pyunicode_to_utf8(s);
            if (!maybe_unicode) {
                return ErrorKind::PyUnicodeError;
            }

            str = *maybe_unicode;
        }
#else
        auto maybe_unicode = pyunicode_to_utf8(s);
        if (!maybe_unicode) {
            return ErrorKind::PyUnicodeError;
        }

        std::string str = std::move(*maybe_unicode);
#endif
        this->emplace(k, str);
    }

    return Result<Key>(k);
};

// Python string object
[[nodiscard]] StringTable::Key
StringTable::key_unsafe(PyObject* s, StringTag tag)
{
    const std::lock_guard<std::mutex> lock(table_lock);

    auto k = make_tagged_key(reinterpret_cast<uintptr_t>(s), tag);

    if (this->find(k) == this->end()) {
#if PY_VERSION_HEX >= 0x030c0000
        // The task name might hold a PyLong for deferred task name formatting.
        auto str =
          (PyLong_CheckExact(s)) ? "Task-" + std::to_string(PyLong_AsLong(s)) : std::string(PyUnicode_AsUTF8(s));
#else
        auto str = std::string(PyUnicode_AsUTF8(s));
#endif
        this->emplace(k, str);
    }

    return k;
};

[[nodiscard]] Result<std::reference_wrapper<const std::string>>
StringTable::lookup(StringTable::Key key) const
{
    const std::lock_guard<std::mutex> lock(table_lock);

    const auto it = this->find(key);
    if (it == this->cend())
        return ErrorKind::LookupError;

    return std::ref(it->second);
};