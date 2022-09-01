from libc.stdint cimport int64_t
from libc.stdint cimport uint16_t
from libc.stdint cimport uint32_t
from libc.stdint cimport uint64_t
from libcpp cimport bool


cdef extern from "include/ddwaf.h" namespace "_ddwaf_config":
    ctypedef struct _ddwaf_config_limits:
        uint32_t max_container_size
        uint32_t max_container_depth
        uint32_t max_string_length

    ctypedef struct _ddwaf_config_obfuscator:
        const char *key_regex
        const char *value_regex


cdef extern from "include/ddwaf.h":
    ctypedef struct ddwaf_version:
        uint16_t major
        uint16_t minor
        uint16_t patch

    void ddwaf_get_version(ddwaf_version *version)

    ctypedef enum DDWAF_OBJ_TYPE:
        DDWAF_OBJ_ARRAY
        DDWAF_OBJ_MAP
        DDWAF_OBJ_STRING
        DDWAF_OBJ_SIGNED
        DDWAF_OBJ_UNSIGNED
        DDWAF_OBJ_INVALID

    ctypedef struct ddwaf_object:
        const char* parameterName
        uint64_t parameterNameLength
        ddwaf_object* array
        const char* stringValue
        uint64_t intValue
        int64_t uintValue
        uint64_t nbEntries
        DDWAF_OBJ_TYPE type

    ctypedef struct ddwaf_ruleset_info:
        uint16_t loaded
        uint16_t failed
        ddwaf_object errors
        const char *version

    ctypedef struct ddwaf_config:
        _ddwaf_config_limits limits
        _ddwaf_config_obfuscator obfuscator

    ctypedef struct ddwaf_handle:
        pass

    ctypedef struct ddwaf_context:
        pass

    ctypedef enum DDWAF_RET_CODE:
        pass

    ctypedef struct ddwaf_result:
        DDWAF_RET_CODE action
        uint64_t total_runtime
        const char* data

    ctypedef void (*ddwaf_object_free_fn)(ddwaf_object *object);

    ddwaf_handle ddwaf_init(const ddwaf_object* rules, const ddwaf_config* config, ddwaf_ruleset_info *info);
    ddwaf_context ddwaf_context_init(const ddwaf_handle handle, ddwaf_object_free_fn obj_free);
    DDWAF_RET_CODE ddwaf_run(ddwaf_context context, ddwaf_object* data, ddwaf_result* result, uint64_t timeout);
    void ddwaf_context_destroy(ddwaf_context context);
    void ddwaf_result_free(ddwaf_result* result);
    void ddwaf_destroy(ddwaf_handle handle);
    const char* const* ddwaf_required_addresses(const ddwaf_handle handle, uint32_t* size);

    ctypedef enum DDWAF_LOG_LEVEL:
        DDWAF_LOG_TRACE

    ctypedef void (*ddwaf_log_fn)(DDWAF_LOG_LEVEL level, const char *function, const char *file, unsigned line, const char *message, uint64_t len);

    void ddwaf_set_log_cb(ddwaf_log_fn fn, int level);
