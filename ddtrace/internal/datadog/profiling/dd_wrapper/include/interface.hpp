#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string_view>

// Forward decl of the return pointer
namespace Datadog {
class Sample;
}

#ifdef __cplusplus
extern "C"
{
#endif
    void ddup_config_env(std::string_view dd_env);
    void ddup_config_service(std::string_view service);
    void ddup_config_version(std::string_view version);
    void ddup_config_runtime(std::string_view runtime);
    void ddup_config_runtime_version(std::string_view runtime_version);
    void ddup_config_profiler_version(std::string_view profiler_version);
    void ddup_config_url(std::string_view url);
    void ddup_config_max_nframes(int max_nframes);

    void ddup_config_user_tag(std::string_view key, std::string_view val);
    void ddup_config_sample_type(unsigned int type);

    bool ddup_is_initialized();
    void ddup_init();
    void ddup_set_runtime_id(std::string_view id);
    bool ddup_upload();

    // Proxy functions to the underlying sample
    Datadog::Sample* ddup_start_sample();
    void ddup_push_walltime(Datadog::Sample* sample, int64_t walltime, int64_t count);
    void ddup_push_cputime(Datadog::Sample* sample, int64_t cputime, int64_t count);
    void ddup_push_acquire(Datadog::Sample* sample, int64_t acquire_time, int64_t count);
    void ddup_push_release(Datadog::Sample* sample, int64_t release_time, int64_t count);
    void ddup_push_alloc(Datadog::Sample* sample, uint64_t size, uint64_t count);
    void ddup_push_heap(Datadog::Sample* sample, uint64_t size);
    void ddup_push_lock_name(Datadog::Sample* sample, std::string_view lock_name);
    void ddup_push_threadinfo(Datadog::Sample* sample,
                              int64_t thread_id,
                              int64_t thread_native_id,
                              std::string_view thread_name);
    void ddup_push_task_id(Datadog::Sample* sample, int64_t task_id);
    void ddup_push_task_name(Datadog::Sample* sample, std::string_view task_name);
    void ddup_push_span_id(Datadog::Sample* sample, int64_t span_id);
    void ddup_push_local_root_span_id(Datadog::Sample* sample, int64_t local_root_span_id);
    void ddup_push_trace_type(Datadog::Sample* sample, std::string_view trace_type);
    void ddup_push_trace_resource_container(Datadog::Sample* sample, std::string_view trace_resource_container);
    void ddup_push_exceptioninfo(Datadog::Sample* sample, std::string_view exception_type, int64_t count);
    void ddup_push_class_name(Datadog::Sample* sample, std::string_view class_name);
    void ddup_push_frame(Datadog::Sample* sample,
                         std::string_view _name,
                         std::string_view _filename,
                         uint64_t address,
                         int64_t line);
    void ddup_flush_sample(Datadog::Sample* sample);
    void ddup_drop_sample(Datadog::Sample* sample);

#ifdef __cplusplus
} // extern "C"
#endif
