#include <echion/long.h>

#if PY_VERSION_HEX >= 0x030c0000
[[nodiscard]] Result<long long>
pylong_to_llong(PyObject* long_addr)
{
    // Only used to extract a task-id on Python 3.12, omits overflow checks
    PyLongObject long_obj;
    long long ret = 0;

    if (copy_type(long_addr, long_obj))
        return ErrorKind::PyLongError;

    if (!PyLong_CheckExact(&long_obj))
        return ErrorKind::PyLongError;

    if (_PyLong_IsCompact(&long_obj)) {
        ret = static_cast<long long>(_PyLong_CompactValue(&long_obj));
    } else {
        // If we're here, then we need to iterate over the digits
        // We might overflow, but we don't care for now
        int sign = _PyLong_NonCompactSign(&long_obj);
        Py_ssize_t i = _PyLong_DigitCount(&long_obj);

        if (i > MAX_DIGITS) {
            return ErrorKind::PyLongError;
        }

        // Copy over the digits as ob_digit is allocated dynamically with
        // PyObject_Malloc.
        digit digits[MAX_DIGITS];
        if (copy_generic(long_obj.long_value.ob_digit, digits, i * sizeof(digit))) {
            return ErrorKind::PyLongError;
        }
        while (--i >= 0) {
            ret <<= PyLong_SHIFT;
            ret |= digits[i];
        }
        ret *= sign;
    }

    return ret;
}
#endif
