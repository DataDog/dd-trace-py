#include <echion/mirrors.h>

[[nodiscard]] Result<MirrorDict>
MirrorDict::create(PyObject* dict_addr)
{
    PyDictObject dict;

    if (copy_type(dict_addr, dict)) {
        return ErrorKind::MirrorError;
    }

    PyDictKeysObject keys;
    if (copy_type(dict.ma_keys, keys)) {
        return ErrorKind::MirrorError;
    }

    // Compute the full dictionary data size
#if PY_VERSION_HEX >= 0x030b0000
    size_t entry_size = keys.dk_kind == DICT_KEYS_UNICODE ? sizeof(PyDictUnicodeEntry) : sizeof(PyDictKeyEntry);
    size_t keys_size = sizeof(PyDictKeysObject) + (1 << keys.dk_log2_index_bytes) + (keys.dk_nentries * entry_size);
#else
    size_t entry_size = sizeof(PyDictKeyEntry);
    size_t keys_size = sizeof(PyDictKeysObject) + (keys.dk_size * sizeof(Py_ssize_t)) + (keys.dk_nentries * entry_size);
#endif
    size_t values_size = dict.ma_values != NULL ? keys.dk_nentries * sizeof(PyObject*) : 0;

    // Allocate the buffer
    ssize_t data_size = keys_size + (keys.dk_nentries * entry_size) + values_size;
    if (data_size < 0 || data_size > MAX_MIRROR_SIZE) {
        return ErrorKind::MirrorError;
    }

    auto data = std::make_unique<char[]>(data_size);

    // Copy the key data and update the pointer
    if (copy_generic(dict.ma_keys, data.get(), keys_size)) {
        return ErrorKind::MirrorError;
    }

    dict.ma_keys = reinterpret_cast<PyDictKeysObject*>(data.get());

    if (dict.ma_values != NULL) {
        // Copy the value data and update the pointer
        char* values_addr = data.get() + keys_size;
        if (copy_generic(dict.ma_values, keys_size, values_size)) {
            return ErrorKind::MirrorError;
        }

        dict.ma_values = reinterpret_cast<PyDictValues*>(values_addr);
    }

    return MirrorDict(dict, std::move(data));
}

[[nodiscard]] Result<MirrorSet>
MirrorSet::create(PyObject* set_addr)
{
    PySetObject set;

    if (copy_type(set_addr, set)) {
        return ErrorKind::MirrorError;
    }

    auto size = set.mask + 1;
    ssize_t table_size = size * sizeof(setentry);
    if (table_size < 0 || table_size > MAX_MIRROR_SIZE) {
        return ErrorKind::MirrorError;
    }

    auto data = std::make_unique<char[]>(table_size);
    if (copy_generic(set.table, data.get(), table_size)) {
        return ErrorKind::MirrorError;
    }

    set.table = reinterpret_cast<setentry*>(data.get());

    return MirrorSet(size, set, std::move(data));
}

[[nodiscard]] Result<std::unordered_set<PyObject*>>
MirrorSet::as_unordered_set()
{
    if (data == nullptr) {
        return ErrorKind::MirrorError;
    }

    std::unordered_set<PyObject*> uset;

    for (size_t i = 0; i < size; i++) {
        auto entry = set.table[i];
        if (entry.key != NULL)
            uset.insert(entry.key);
    }

    return uset;
}
