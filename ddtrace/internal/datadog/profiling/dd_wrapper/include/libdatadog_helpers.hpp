#pragma once

#include <array>
#include <iostream>
#include <string>
#include <string_view>
#include <variant>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

// There's currently no need to offer custom tags, so there's no interface for
// it.  Instead, tags are keyed and populated based on this table, then
// referenced in `add_tag()`.
// There are two columns because runtime-id has a dash, which can't be used
// within a C++ symbol name.
#define EXPORTER_TAGS(X)                                                                                               \
    X(language, "language")                                                                                            \
    X(dd_env, "env")                                                                                                   \
    X(service, "service")                                                                                              \
    X(version, "version")                                                                                              \
    X(runtime_version, "runtime_version")                                                                              \
    X(runtime, "runtime")                                                                                              \
    X(runtime_id, "runtime-id")                                                                                        \
    X(profiler_version, "profiler_version")                                                                            \
    X(library_version, "library_version")                                                                              \
    X(profile_seq, "profile_seq")                                                                                      \
    X(is_crash, "is_crash")                                                                                            \
    X(severity, "severity")

// Here there are two columns because the Datadog backend expects these labels
// to have spaces in the names.
#define EXPORTER_LABELS(X)                                                                                             \
    X(exception_type, "exception type")                                                                                \
    X(thread_id, "thread id")                                                                                          \
    X(thread_native_id, "thread native id")                                                                            \
    X(thread_name, "thread name")                                                                                      \
    X(task_id, "task id")                                                                                              \
    X(task_name, "task name")                                                                                          \
    X(span_id, "span id")                                                                                              \
    X(local_root_span_id, "local root span id")                                                                        \
    X(trace_type, "trace type")                                                                                        \
    X(class_name, "class name")                                                                                        \
    X(lock_name, "lock name")                                                                                          \
    X(gpu_device_name, "gpu device name")

#define X_ENUM(a, b) a,
#define X_STR(a, b) b,

enum class ExportTagKey
{
    EXPORTER_TAGS(X_ENUM) Length_
};

enum class ExportLabelKey
{
    EXPORTER_LABELS(X_ENUM) Length_
};

inline ddog_CharSlice
to_slice(std::string_view str)
{
    return { .ptr = str.data(), .len = str.size() };
}

inline ddog_ByteSlice
to_byte_slice(std::string_view str)
{
    return { .ptr = reinterpret_cast<const uint8_t*>(str.data()), .len = str.size() };
}

inline std::string
err_to_msg(const ddog_Error* err, std::string_view msg)
{
    auto ddog_err = ddog_Error_message(err);
    std::string err_msg;
    return std::string{ msg } + " (" + err_msg.assign(ddog_err.ptr, ddog_err.ptr + ddog_err.len) + ")";
}

inline std::string_view
to_string(ExportTagKey key)
{
    constexpr auto num_keys = static_cast<size_t>(ExportTagKey::Length_);
    constexpr std::array<std::string_view, num_keys> keys = { EXPORTER_TAGS(X_STR) };
    constexpr std::string_view invalid; // just the empty string (but can be referenced)

    if (static_cast<size_t>(key) >= num_keys) {
        return invalid;
    }
    return keys[static_cast<size_t>(key)];
}

inline std::string_view
to_string(ExportLabelKey key)
{
    constexpr auto num_keys = static_cast<size_t>(ExportLabelKey::Length_);
    constexpr std::array<std::string_view, num_keys> keys = { EXPORTER_LABELS(X_STR) };
    constexpr std::string_view invalid; // just the empty string (but can be referenced)

    if (static_cast<size_t>(key) >= num_keys) {
        return invalid;
    }
    return keys[static_cast<size_t>(key)];
}

inline bool
add_tag(ddog_Vec_Tag& tags, std::string_view key, std::string_view val, std::string& errmsg)
{
    static bool already_warned = false;
    if (key.empty() || val.empty()) {
        return false;
    }

    ddog_Vec_Tag_PushResult res = ddog_Vec_Tag_push(&tags, to_slice(key), to_slice(val));
    if (res.tag == DDOG_VEC_TAG_PUSH_RESULT_ERR) {
        if (!already_warned) {
            already_warned = true;
            errmsg = err_to_msg(&res.err, "");
            std::cerr << errmsg << std::endl;
        }
        ddog_Error_drop(&res.err);
        return false;
    }
    return true;
}

inline bool
add_tag(ddog_Vec_Tag& tags, const ExportTagKey key, std::string_view val, std::string& errmsg)
{
    const std::string_view key_sv = to_string(key);
    if (val.empty() || key_sv.empty()) {
        return false;
    }

    return add_tag(tags, key_sv, val, errmsg);
}

// Keep macros from propagating
#undef X_STR
#undef X_ENUM
#undef EXPORTER_TAGS
#undef EXPORTER_LABELS

} // namespace Datadog
