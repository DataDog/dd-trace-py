// This file is part of "echion" which is released under MIT.
//
// Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.

#pragma once

#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <dictobject.h>
#include <setobject.h>

#include <memory>

#if PY_VERSION_HEX >= 0x030b0000
#define Py_BUILD_CORE
#if defined __GNUC__ && defined HAVE_STD_ATOMIC
#undef HAVE_STD_ATOMIC
#endif
#include <internal/pycore_dict.h>
#else
typedef struct
{
    Py_hash_t me_hash;
    PyObject* me_key;
    PyObject* me_value; /* This field is only meaningful for combined tables */
} PyDictKeyEntry;

typedef Py_ssize_t (*dict_lookup_func)(PyDictObject* mp, PyObject* key, Py_hash_t hash,
                                       PyObject** value_addr);

/* See dictobject.c for actual layout of DictKeysObject */
typedef struct _dictkeysobject
{
    Py_ssize_t dk_refcnt;

    /* Size of the hash table (dk_indices). It must be a power of 2. */
    Py_ssize_t dk_size;

    dict_lookup_func dk_lookup;

    /* Number of usable entries in dk_entries. */
    Py_ssize_t dk_usable;

    /* Number of used entries in dk_entries. */
    Py_ssize_t dk_nentries;

    char dk_indices[]; /* char is required to avoid strict aliasing. */

} PyDictKeysObject;

typedef PyObject* PyDictValues;
#endif

#include <exception>
#include <unordered_set>

#include <echion/vm.h>

class MirrorError : public std::exception
{
public:
    const char* what() const noexcept override
    {
        return "Cannot create mirror object";
    }
};

class MirrorObject
{
public:
    inline PyObject* reflect()
    {
        if (reflected == NULL)
            throw MirrorError();
        return reflected;
    }

protected:
    std::unique_ptr<char[]> data = nullptr;
    PyObject* reflected = NULL;
};

// ----------------------------------------------------------------------------
class MirrorDict : public MirrorObject
{
public:
    MirrorDict(PyObject*);

    PyObject* get_item(PyObject* key)
    {
        return PyDict_GetItem(reflect(), key);
    }

private:
    PyDictObject dict;
};

inline MirrorDict::MirrorDict(PyObject* dict_addr)
{
    if (copy_type(dict_addr, dict))
        throw MirrorError();

    PyDictKeysObject keys;
    if (copy_type(dict.ma_keys, keys))
        throw MirrorError();

    // Compute the full dictionary data size
#if PY_VERSION_HEX >= 0x030b0000
    size_t entry_size =
        keys.dk_kind == DICT_KEYS_UNICODE ? sizeof(PyDictUnicodeEntry) : sizeof(PyDictKeyEntry);
    size_t keys_size = sizeof(PyDictKeysObject) + (1 << keys.dk_log2_index_bytes) +
                       (keys.dk_nentries * entry_size);
#else
    size_t entry_size = sizeof(PyDictKeyEntry);
    size_t keys_size = sizeof(PyDictKeysObject) + (keys.dk_size * sizeof(Py_ssize_t)) +
                       (keys.dk_nentries * entry_size);
#endif
    size_t values_size = dict.ma_values != NULL ? keys.dk_nentries * sizeof(PyObject*) : 0;

    // Allocate the buffer
    ssize_t data_size = keys_size + (keys.dk_nentries * entry_size) + values_size;
    if (data_size < 0 || data_size > (1 << 20))
        throw MirrorError();

    data = std::make_unique<char[]>(data_size);

    // Copy the key data and update the pointer
    if (copy_generic(dict.ma_keys, data.get(), keys_size))
        throw MirrorError();

    dict.ma_keys = (PyDictKeysObject*)data.get();

    if (dict.ma_values != NULL)
    {
        // Copy the value data and update the pointer
        char* values_addr = data.get() + keys_size;
        if (copy_generic(dict.ma_values, keys_size, values_size))
            throw MirrorError();

        dict.ma_values = (PyDictValues*)values_addr;
    }

    reflected = (PyObject*)&dict;
}

// ----------------------------------------------------------------------------
class MirrorSet : public MirrorObject
{
public:
    MirrorSet(PyObject*);
    std::unordered_set<PyObject*> as_unordered_set();

private:
    size_t size;
    PySetObject set;
};

inline MirrorSet::MirrorSet(PyObject* set_addr)
{
    if (copy_type(set_addr, set))
        throw MirrorError();

    size = set.mask + 1;
    ssize_t table_size = size * sizeof(setentry);
    if (table_size < 0 || table_size > (1 << 20))
        throw MirrorError();

    data = std::make_unique<char[]>(table_size);
    if (copy_generic(set.table, data.get(), table_size))
        throw MirrorError();

    set.table = (setentry*)data.get();

    reflected = (PyObject*)&set;
}

inline std::unordered_set<PyObject*> MirrorSet::as_unordered_set()
{
    if (data == nullptr)
        throw MirrorError();

    std::unordered_set<PyObject*> uset;

    for (size_t i = 0; i < size; i++)
    {
        auto entry = set.table[i];
        if (entry.key != NULL)
            uset.insert(entry.key);
    }

    return uset;
}
