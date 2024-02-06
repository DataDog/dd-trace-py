#pragma once

#include <array>
#include <iostream>
#include <string>
#include <string_view>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

// There's currently no need to offer custom tags, so there's no interface for
// it.  Instead, tags are keyed and populated based on this table, then
// referenced in `add_tag()`.
// There are two columns because runtime-id has a dash.
#define EXPORTER_TAGS(X)                                                                                               \
    X(language, "language")                                                                                            \
    X(env, "env")                                                                                                      \
    X(service, "service")                                                                                              \
    X(version, "version")                                                                                              \
    X(runtime_version, "runtime_version")                                                                              \
    X(runtime, "runtime")                                                                                              \
    X(runtime_id, "runtime-id")                                                                                        \
    X(profiler_version, "profiler_version")                                                                            \
    X(profile_seq, "profile_seq")

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
    X(trace_resource_container, "trace resource container")                                                            \
    X(trace_endpoint, "trace endpoint")                                                                                \
    X(class_name, "class name")                                                                                        \
    X(lock_name, "lock name")

#define X_ENUM(a, b) a,
#define X_STR(a, b) b,

enum class ExportTagKey
{
    EXPORTER_TAGS(X_ENUM) _Length
};

enum class ExportLabelKey
{
    EXPORTER_LABELS(X_ENUM) _Length
};

// Utility functions
inline ddog_CharSlice
to_slice(std::string_view str)
{
    return { .ptr = str.data(), .len = str.size() };
}

inline std::string
err_to_msg(const ddog_Error* err, std::string_view msg)
{
    auto ddog_err = ddog_Error_message(err);
    std::string err_msg;
    return std::string{ msg } + "(" + err_msg.assign(ddog_err.ptr, ddog_err.ptr + ddog_err.len) + ")";
}

inline static bool
add_tag(ddog_Vec_Tag& tags, const ExportTagKey key, std::string_view val, std::string& errmsg)
{
    // NB the storage of `val` needs to be guaranteed until the tags are flushed
    constexpr size_t num_keys = static_cast<size_t>(ExportTagKey::_Length);
    constexpr std::array<std::string_view, num_keys> keys = { EXPORTER_TAGS(X_STR) };
    std::string_view key_sv = keys[static_cast<size_t>(key)];

    // Can't add empty tags. This isn't an error.
    if (val.empty())
        return true;

    // Add
    ddog_Vec_Tag_PushResult res = ddog_Vec_Tag_push(&tags, to_slice(key_sv), to_slice(val));
    if (res.tag == DDOG_VEC_TAG_PUSH_RESULT_ERR) {
        errmsg = err_to_msg(&res.err, "Error pushing tag");
        errmsg += "(val:'" + std::string(val) + "')";
        ddog_Error_drop(&res.err);
        return false;
    }
    return true;
}

inline static bool
add_tag_unsafe(ddog_Vec_Tag& tags, std::string_view key, std::string_view val, std::string& errmsg)
{
    if (key.empty() || val.empty())
        return false;

    ddog_Vec_Tag_PushResult res = ddog_Vec_Tag_push(&tags, to_slice(key), to_slice(val));
    if (res.tag == DDOG_VEC_TAG_PUSH_RESULT_ERR) {
        errmsg = err_to_msg(&res.err, "Error pushing tag (unsafe)");
        ddog_Error_drop(&res.err);
        std::cout << errmsg << std::endl;
        return false;
    }
    return true;
}

inline static std::string_view
str_from_key(const ExportLabelKey key)
{
    constexpr std::array<std::string_view, static_cast<size_t>(ExportLabelKey::_Length)> keys = { EXPORTER_LABELS(
      X_STR) };

    // Handle invalid keys
    if (static_cast<size_t>(key) >= keys.size())
        return "";

    return keys[static_cast<size_t>(key)];
}

// Keep macros from propagating
#undef X_STR
#undef X_ENUM
#undef EXPORTER_TAGS
#undef EXPORTER_LABELS

} // namespace Datadog
