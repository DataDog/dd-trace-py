#include "interface.hpp"
#include "libdatadog_helpers.hpp"
#include "profile.hpp"
#include "sample.hpp"
#include "sample_builder.hpp"
#include "uploader.hpp"
#include "uploader_builder.hpp"

#include <cstdlib>
#include <iostream>
#include <unistd.h>

// State
bool is_ddup_initialized = false;
bool g_prof_flag = true;

// Each sampler needs to write to its own private area, which is then merged
// into a global ddog_prof_Profile. After a `fork()`, this Sample will be non-
// addressable.  Currently, we make no attempt at handling this case.
// DAS24.02.06: I think pthreads allocates TLS on new pages, so CoW would ensure
//              the overhead isn't attributable to the child.  Nevertheless,
//              any access on the page would invalidate this assumption.
thread_local std::optional<Datadog::Sample> g_profile = std::nullopt;

// When a fork is detected, we need to reinitialize this state.
// This handler will be called in the single thread of the child process after the fork
void
ddup_fork_handler()
{
    Datadog::Profile::mark_dirty();
}

// Configuration
void
ddup_config_env(const char* env)
{
    if (env)
        Datadog::UploaderBuilder::set_env(env);
}
void
ddup_config_service(const char* service)
{
    if (service)
        Datadog::UploaderBuilder::set_service(service);
}
void
ddup_config_version(const char* version)
{
    if (version)
        Datadog::UploaderBuilder::set_version(version);
}
void
ddup_config_runtime(const char* runtime)
{
    if (runtime)
        Datadog::UploaderBuilder::set_runtime(runtime);
}
void
ddup_config_runtime_version(const char* runtime_version)
{
    if (runtime_version)
        Datadog::UploaderBuilder::set_runtime_version(runtime_version);
}
void
ddup_config_profiler_version(const char* profiler_version)
{
    if (profiler_version)
        Datadog::UploaderBuilder::set_profiler_version(profiler_version);
}
void
ddup_config_url(const char* url)
{
    if (url)
        Datadog::UploaderBuilder::set_url(url);
}
void
ddup_config_user_tag(const char* key, const char* val)
{
    if (key && val)
        Datadog::UploaderBuilder::set_tag(key, val);
}
void
ddup_config_sample_type(unsigned int _type)
{
    Datadog::SampleBuilder::add_type(_type);
}
void
ddup_config_max_nframes(int max_nframes)
{
    Datadog::SampleBuilder::set_max_nframes(max_nframes);
}

bool
ddup_is_initialized()
{
    return is_ddup_initialized;
}

void
ddup_init()
{
    static std::atomic<int> initialized_count{ 0 };
    static bool initialized = []() {
        // install the ddup_fork_handler for pthread_atfork
        // Right now, only do things in the child _after_ fork
        pthread_atfork(nullptr, nullptr, ddup_fork_handler);

        // Set the global initialization flag
        is_ddup_initialized = true;
        return true;
    }();

    initialized_count.fetch_add(static_cast<int>(initialized));
    if (initialized_count > 1) {
        std::cerr << "ddup_init() called " << initialized_count << " times" << std::endl;
    }
}

void
ddup_start_sample()
{
    if (!g_profile)
        g_profile = Datadog::SampleBuilder::build();
    g_profile->start_sample();
}

void
ddup_push_walltime(int64_t walltime, int64_t count)
{
    g_profile->push_walltime(walltime, count);
}

void
ddup_push_cputime(int64_t cputime, int64_t count)
{
    g_profile->push_cputime(cputime, count);
}

void
ddup_push_acquire(int64_t acquire_time, int64_t count)
{
    g_profile->push_acquire(acquire_time, count);
}

void
ddup_push_release(int64_t release_time, int64_t count)
{
    g_profile->push_release(release_time, count);
}

void
ddup_push_alloc(uint64_t size, uint64_t count)
{
    g_profile->push_alloc(size, count);
}

void
ddup_push_heap(uint64_t size)
{
    g_profile->push_heap(size);
}

void
ddup_push_lock_name(const char* lock_name)
{
    g_profile->push_lock_name(lock_name);
}

void
ddup_push_threadinfo(int64_t thread_id, int64_t thread_native_id, const char* thread_name)
{
    g_profile->push_threadinfo(thread_id, thread_native_id, thread_name);
}

void
ddup_push_task_id(int64_t task_id)
{
    g_profile->push_task_id(task_id);
}

void
ddup_push_task_name(const char* task_name)
{
    g_profile->push_task_name(task_name);
}

void
ddup_push_span_id(int64_t span_id)
{
    g_profile->push_span_id(span_id);
}

void
ddup_push_local_root_span_id(int64_t local_root_span_id)
{
    g_profile->push_local_root_span_id(local_root_span_id);
}

void
ddup_push_trace_type(const char* trace_type)
{
    g_profile->push_trace_type(trace_type);
}

void
ddup_push_trace_resource_container(const char* trace_resource_container)
{
    g_profile->push_trace_resource_container(trace_resource_container);
}

void
ddup_push_exceptioninfo(const char* exception_type, int64_t count)
{
    g_profile->push_exceptioninfo(exception_type, count);
}

void
ddup_push_class_name(const char* class_name)
{
    g_profile->push_class_name(class_name);
}

void
ddup_push_frame(const char* name, const char* fname, uint64_t address, int64_t line)
{
    g_profile->push_frame(name, fname, address, line);
}

void
ddup_flush_sample()
{
    g_profile->flush_sample();
}

void
ddup_set_runtime_id(const char* id, size_t sz)
{
    Datadog::UploaderBuilder::set_runtime_id(std::string_view(id, sz));
}

bool
ddup_upload()
{
    if (!is_ddup_initialized) {
        std::cerr << "ddup_upload() called before ddup_init()" << std::endl;
        return false;
    }

    ddog_prof_Profile upload_profile = Datadog::Sample::get_ddog_profile();
    Datadog::Profile::mark_dirty();

    // We create a new uploader just for this operation
    bool success = false;
    try {
        auto uploader = Datadog::UploaderBuilder::build();
        success = uploader.upload(upload_profile);
    } catch (const std::exception& e) {
        std::cerr << "Failed to create uploader: " << e.what() << std::endl;
        return false;
    }
    return success;
}

void
ddup_cleanup()
{
    // TODO some other stuff probably goes here.
    Datadog::Sample::reset_profile();
}
