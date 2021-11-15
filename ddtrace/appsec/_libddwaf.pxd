from libcpp cimport bool
from libc.stdint cimport uint16_t, int64_t, uint64_t


cdef extern from "include/ddwaf.h":
    ctypedef struct ddwaf_version:
        uint16_t major
        uint16_t minor
        uint16_t patch

    void ddwaf_get_version(ddwaf_version *version)

    ctypedef struct ddwaf_object:
        const char* parameterName
        uint64_t parameterNameLength
        ddwaf_object* array
        uint64_t nbEntries

    ddwaf_object* ddwaf_object_invalid(ddwaf_object *obj);
    ddwaf_object* ddwaf_object_stringl_nc(ddwaf_object *obj, const char *val, size_t length);
    ddwaf_object* ddwaf_object_array(ddwaf_object *obj);
    ddwaf_object* ddwaf_object_map(ddwaf_object *obj);
