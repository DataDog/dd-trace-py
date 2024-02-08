#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C"
{
#endif
    void ddup_config_env(const char* env);
    void ddup_config_service(const char* service);
    void ddup_config_version(const char* version);
    void ddup_config_runtime(const char* runtime);
    void ddup_config_runtime_version(const char* runtime_version);
    void ddup_config_profiler_version(const char* profiler_version);
    void ddup_config_url(const char* url);
    void ddup_config_max_nframes(int max_nframes);

    void ddup_config_user_tag(const char* key, const char* val);
    void ddup_config_sample_type(unsigned int type);

    bool ddup_is_initialized();
    void ddup_init();

    unsigned int ddup_start_sample(unsigned int requested);
    void ddup_push_walltime(unsigned int handle, int64_t walltime, int64_t count);
    void ddup_push_cputime(unsigned int handle, int64_t cputime, int64_t count);
    void ddup_push_acquire(unsigned int handle, int64_t acquire_time, int64_t count);
    void ddup_push_release(unsigned int handle, int64_t release_time, int64_t count);
    void ddup_push_alloc(unsigned int handle, uint64_t size, uint64_t count);
    void ddup_push_heap(unsigned int handle, uint64_t size);
    void ddup_push_lock_name(unsigned int handle, const char* lock_name);
    void ddup_push_threadinfo(unsigned int handle,
                              int64_t thread_id,
                              int64_t thread_native_id,
                              const char* thread_name);
    void ddup_push_task_id(unsigned int handle, int64_t task_id);
    void ddup_push_task_name(unsigned int handle, const char* task_name);
    void ddup_push_span_id(unsigned int handle, int64_t span_id);
    void ddup_push_local_root_span_id(unsigned int handle, int64_t local_root_span_id);
    void ddup_push_trace_type(unsigned int handle, const char* trace_type);
    void ddup_push_trace_resource_container(unsigned int handle, const char* trace_resource_container);
    void ddup_push_exceptioninfo(unsigned int handle, const char* exception_type, int64_t count);
    void ddup_push_class_name(unsigned int handle, const char* class_name);
    void ddup_push_frame(unsigned int handle, const char* _name, const char* _filename, uint64_t address, int64_t line);
    void ddup_flush_sample(unsigned int handle);
    void ddup_set_runtime_id(unsigned int handle, const char* id, size_t sz);
    bool ddup_upload();

    // Unusual interfaces (testing, mostly)
    void ddup_cleanup();

#ifdef __cplusplus
} // extern "C"
#endif
