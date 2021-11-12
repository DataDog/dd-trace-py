from libcpp cimport bool
from libc.stdint cimport uint16_t, int64_t, uint64_t


cdef extern from "include/ddwaf.h":
    ctypedef struct ddwaf_version:
        uint16_t major
        uint16_t minor
        uint16_t patch

    void ddwaf_get_version(ddwaf_version *version)

    ctypedef struct ddwaf_object:
        pass

    ddwaf_object* ddwaf_object_invalid(ddwaf_object *obj);
    ddwaf_object* ddwaf_object_string(ddwaf_object *obj, const char *val);
    ddwaf_object* ddwaf_object_stringl(ddwaf_object *obj, const char *val, size_t length);
    ddwaf_object* ddwaf_object_stringl_nc(ddwaf_object *obj, const char *val, size_t length);
    ddwaf_object* ddwaf_object_signed(ddwaf_object *obj, int64_t val);
    ddwaf_object* ddwaf_object_unsigned(ddwaf_object *obj, uint64_t val);
    ddwaf_object* ddwaf_object_signed_force(ddwaf_object *obj, int64_t val);
    ddwaf_object* ddwaf_object_unsigned_force(ddwaf_object *obj, uint64_t val);
    ddwaf_object* ddwaf_object_array(ddwaf_object *obj);
    bool ddwaf_object_array_add(ddwaf_object *array, ddwaf_object *obj);
    ddwaf_object* ddwaf_object_map(ddwaf_object *obj);
    bool ddwaf_object_map_add(ddwaf_object *map, const char *key, ddwaf_object *obj);
    bool ddwaf_object_map_addl(ddwaf_object *map, const char *key, size_t length, ddwaf_object *obj);
    bool ddwaf_object_map_addl_nc(ddwaf_object *map, const char *key, size_t length, ddwaf_object *obj);
    void ddwaf_object_free(ddwaf_object *obj);
