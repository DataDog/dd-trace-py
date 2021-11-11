from libc.stdint cimport uint16_t


cdef extern from "include/ddwaf.h":
    ctypedef struct ddwaf_version:
        uint16_t major
        uint16_t minor
        uint16_t patch

    void ddwaf_get_version(ddwaf_version *version)
