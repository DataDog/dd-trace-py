#include <echion/mirrors.h>

[[nodiscard]] Result<MirrorSet>
MirrorSet::create(PyObject* set_addr)
{
    PySetObject set;

    if (copy_type(set_addr, set)) {
        return ErrorKind::MirrorError;
    }

    // Validate mask before adding 1 then multiplying to prevent signed integer overflow.
    // The overflow could happen on the +1, or on the *sizeof(setentry), which either
    // way would wrap to a negative value and cause memory issues.
    if (set.mask < 0 || set.mask >= MAX_MIRROR_ITEMS) {
        return ErrorKind::MirrorError;
    }
    auto size = set.mask + 1;

    ssize_t table_size = static_cast<ssize_t>(size * sizeof(setentry));
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
