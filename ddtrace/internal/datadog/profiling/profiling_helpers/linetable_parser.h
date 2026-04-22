#pragma once

#include <cstdint>

/* Shared line table parsing for profiling.
 *
 * Provides allocation-free, GIL-free line number resolution from
 * CPython's co_linetable (3.10+ PEP 626/657) and co_lnotab (3.9).
 *
 * All functions perform pure byte-parsing with no Python API calls,
 * making them safe for both:
 *   - memalloc's allocator hook (allocation-free requirement)
 *   - echion's sampling thread (no-GIL requirement)
 */

#include "version_compat.h"

namespace DataDog {

/* ---- Varint helpers for PEP 657 location table (Python 3.11+) ---------- */
#if PY_VERSION_HEX >= 0x030b0000

inline int
read_varint(const unsigned char* table, Py_ssize_t len, Py_ssize_t* i)
{
    Py_ssize_t guard = len - 1;
    if (*i >= guard)
        return 0;

    uint32_t val = table[++*i] & 63;
    uint32_t shift = 0;
    while (*i < guard && table[*i] & 64) {
        shift += 6;

        if (shift >= 32) {
            // Guard against UB from over-large shift on malformed input;
            // advance `i` past remaining continuation bytes to leave it in a
            // consistent state, even though callers don't rely on the value of
            // i or reuse it
            for (; *i < guard && table[*i] & 64; ++*i)
                ;

            return 0;
        }

        val |= static_cast<uint32_t>(table[++*i] & 63) << shift;
    }
    return static_cast<int>(val);
}

inline int
read_signed_varint(const unsigned char* table, Py_ssize_t len, Py_ssize_t* i)
{
    uint32_t val = static_cast<uint32_t>(read_varint(table, len, i));
    return (val & 1) ? -static_cast<int>(val >> 1) : static_cast<int>(val >> 1);
}

#endif /* PY_VERSION_HEX >= 0x030b0000 */

/* ---- Line table parser ------------------------------------------------- */

/* Parse a CPython line table and return the line number for the given
 * bytecode offset.
 *
 * Parameters:
 *   table       — pointer to the raw line table bytes
 *   len         — length of the table in bytes
 *   lasti       — last instruction index:
 *                   Python 3.11+: in _Py_CODEUNIT units
 *                   Python 3.10:  in _Py_CODEUNIT units (converted internally)
 *                   Python 3.9:   byte offset
 *   firstlineno — co_firstlineno from the code object
 *
 * The caller is responsible for obtaining the table bytes and lasti
 * from the appropriate code-object fields.  The version-specific
 * table format is handled internally.
 *
 * Returns the resolved line number, or 0 if it cannot be determined. */
inline int
parse_linetable(const unsigned char* table, Py_ssize_t len, int lasti, int firstlineno)
{
    if (lasti < 0) {
        return firstlineno;
    }

    unsigned int lineno = static_cast<unsigned int>(firstlineno);

#if PY_VERSION_HEX >= 0x030b0000
    /* Python 3.11+: PEP 657 location table in co_linetable.
     * Each entry byte: bits[2:0] = (codeunit_delta - 1), bits[6:3] = info code.
     * lasti is in _Py_CODEUNIT units, matching the table's bc counter. */
    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        bc += (table[i] & 7) + 1;
        int info_code = (table[i] >> 3) & 15;
        switch (info_code) {
            case 15: /* No location info */
                break;
            case 14: /* Long form: signed varint line delta + 3 varints */
                lineno += read_signed_varint(table, len, &i);
                read_varint(table, len, &i); /* end_line */
                read_varint(table, len, &i); /* column */
                read_varint(table, len, &i); /* end_column */
                break;
            case 13: /* No column data: signed varint line delta */
                lineno += read_signed_varint(table, len, &i);
                break;
            case 12:
            case 11:
            case 10: /* New lineno: delta = info_code - 10, skip 2 column bytes */
                lineno += info_code - 10;
                if (i < len - 2)
                    i += 2;
                break;
            default: /* Same line, skip 1 column byte */
                if (i < len - 1)
                    i += 1;
                break;
        }
        if (bc > lasti)
            break;
    }

#elif PY_VERSION_HEX >= 0x030a0000
    /* Python 3.10: PEP 626 line table in co_linetable.
     * Pairs of (sdelta, ldelta) bytes.  lasti is in codeunit units;
     * the table bytecode deltas are in byte units, so convert. */

    /* Check for even-ness as we expect pairs of (sdelta, ldelta) bytes.
     * This precondition is not guaranteed when using data copied from
     * the process and not "actual" code objects. */
    if (len % 2 != 0)
        return 0;

    lasti *= static_cast<int>(sizeof(_Py_CODEUNIT));
    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        int sdelta = table[i++];
        if (sdelta == 0xff)
            break;
        bc += sdelta;
        int ldelta = table[i];
        if (ldelta == 0x80)
            ldelta = 0;
        else if (ldelta > 0x80)
            lineno -= 0x100;
        lineno += ldelta;
        if (bc > lasti)
            break;
    }

#else
    /* Python 3.9: co_lnotab format — pairs of (bytecode_delta, line_delta)
     * unsigned bytes.  lasti is a byte offset. */

    /* Check for even-ness as we expect pairs of (bytecode_delta, line_delta) bytes.
     * This precondition is not guaranteed when using data copied from
     * the process and not "actual" code objects. */
    if (len % 2 != 0)
        return 0;

    for (Py_ssize_t i = 0, bc = 0; i < len; i++) {
        bc += table[i++];
        if (bc > lasti)
            break;
        if (table[i] >= 0x80)
            lineno -= 0x100;
        lineno += table[i];
    }

#endif /* PY_VERSION_HEX >= 0x030b0000 */

    return lineno > 0 ? static_cast<int>(lineno) : 0;
}

} /* namespace DataDog */
