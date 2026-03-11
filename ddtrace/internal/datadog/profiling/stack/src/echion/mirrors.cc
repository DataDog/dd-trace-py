#include <echion/mirrors.h>

[[nodiscard]] Result<MirrorSet>
MirrorSet::create(PyObject* set_addr)
{
    PySetObject set;

    if (copy_type(set_addr, set)) {
        return ErrorKind::MirrorError;
    }

    auto size = set.mask + 1;
    // Validate size before multiplication to prevent integer overflow.
    // Without this check, a large mask value could cause size * sizeof(setentry)
    // to wrap around to a small value, passing the MAX_MIRROR_SIZE check while
    // the actual size used in iteration remains huge.
    if (size <= 0 || size > MAX_MIRROR_ITEMS) {
        return ErrorKind::MirrorError;
    }

    ssize_t table_size = size * sizeof(setentry);
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
