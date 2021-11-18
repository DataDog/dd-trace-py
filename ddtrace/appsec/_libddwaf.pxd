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

    ctypedef struct ddwaf_handle:
        pass

    ctypedef struct ddwaf_config:
        pass

    ctypedef struct ddwaf_context:
        pass

    ctypedef struct ddwaf_result:
        pass

    ctypedef enum DDWAF_RET_CODE:
        pass

    ctypedef void (*ddwaf_object_free_fn)(ddwaf_object *object);

    ddwaf_handle ddwaf_init(const ddwaf_object* rules, const ddwaf_config* config);
    ddwaf_context ddwaf_context_init(const ddwaf_handle handle, ddwaf_object_free_fn obj_free);
    DDWAF_RET_CODE ddwaf_run(ddwaf_context context, ddwaf_object* data, ddwaf_result *result, uint64_t timeout);
    void ddwaf_context_destroy(ddwaf_context context);
    void ddwaf_destroy(ddwaf_handle handle);
