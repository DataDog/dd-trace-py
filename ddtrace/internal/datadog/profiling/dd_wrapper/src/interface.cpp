#include "interface.hpp"
#include "libdatadog_helpers.hpp"
#include "profile.hpp"
#include "sample.hpp"
#include "sample_manager.hpp"
#include "uploader.hpp"
#include "uploader_builder.hpp"

#include <cstdlib>
#include <iostream>
#include <unistd.h>

// State
bool is_ddup_initialized = false;

// When a fork is detected, we need to reinitialize this state.
// This handler will be called in the single thread of the child process after the fork
void
ddup_postfork_child()
{
    Datadog::Profile::mark_dirty();
    Datadog::Uploader::unlock();
}

void
ddup_postfork_parent()
{
    Datadog::Uploader::unlock();
}

// Since we don't control the internal state of libdatadog's exporter and we want to prevent state-tearing
// after a `fork()`, we just outright prevent uploads from happening during a `fork()` operation.  Since a
// new upload may be scheduled _just_ as `fork()` is entered, we also need to prevent new uploads from happening.
// This mutex is released in both the child and the parent.
void
ddup_prefork()
{
    Datadog::Uploader::lock();
    Datadog::Uploader::cancel_inflight();
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
    Datadog::SampleManager::add_type(_type);
}
void
ddup_config_max_nframes(int max_nframes)
{
    Datadog::SampleManager::set_max_nframes(max_nframes);
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
        pthread_atfork(ddup_prefork, ddup_postfork_parent, ddup_postfork_child);

        // Set the global initialization flag
        is_ddup_initialized = true;
        return true;
    }();

    initialized_count.fetch_add(static_cast<int>(initialized));
    if (initialized_count > 1) {
        std::cerr << "ddup_init() called " << initialized_count << " times" << std::endl;
    }
}

unsigned int
ddup_start_sample(unsigned int requested)
{
    return static_cast<unsigned int>(Datadog::SampleManager::start_sample(requested));
}

void
ddup_set_runtime_id(const char* id, size_t sz)
{
    Datadog::UploaderBuilder::set_runtime_id(std::string_view(id, sz));
}

void
ddup_push_walltime(unsigned int _handle, int64_t walltime, int64_t count)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_walltime(handle, walltime, count);
}

void
ddup_push_cputime(unsigned int _handle, int64_t cputime, int64_t count)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_cputime(handle, cputime, count);
}

void
ddup_push_acquire(unsigned int _handle, int64_t acquire_time, int64_t count)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_acquire(handle, acquire_time, count);
}

void
ddup_push_release(unsigned int _handle, int64_t release_time, int64_t count)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_release(handle, release_time, count);
}

void
ddup_push_alloc(unsigned int _handle, uint64_t size, uint64_t count)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_alloc(handle, size, count);
}

void
ddup_push_heap(unsigned int _handle, uint64_t size)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_heap(handle, size);
}

void
ddup_push_lock_name(unsigned int _handle, const char* lock_name)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_lock_name(handle, lock_name);
}

void
ddup_push_threadinfo(unsigned int _handle, int64_t thread_id, int64_t thread_native_id, const char* thread_name)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_threadinfo(handle, thread_id, thread_native_id, thread_name);
}

void
ddup_push_task_id(unsigned int _handle, int64_t task_id)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_task_id(handle, task_id);
}

void
ddup_push_task_name(unsigned int _handle, const char* task_name)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_task_name(handle, task_name);
}

void
ddup_push_span_id(unsigned int _handle, int64_t span_id)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_span_id(handle, span_id);
}

void
ddup_push_local_root_span_id(unsigned int _handle, int64_t local_root_span_id)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_local_root_span_id(handle, local_root_span_id);
}

void
ddup_push_trace_type(unsigned int _handle, const char* trace_type)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_trace_type(handle, trace_type);
}

void
ddup_push_trace_resource_container(unsigned int _handle, const char* trace_resource_container)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_trace_resource_container(handle, trace_resource_container);
}

void
ddup_push_exceptioninfo(unsigned int _handle, const char* exception_type, int64_t count)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_exceptioninfo(handle, exception_type, count);
}

void
ddup_push_class_name(unsigned int _handle, const char* class_name)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_class_name(handle, class_name);
}

void
ddup_push_frame(unsigned int _handle, const char* _name, const char* _filename, uint64_t address, int64_t line)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::push_frame(handle, _name, _filename, address, line);
}

void
ddup_flush_sample(unsigned int _handle)
{
    auto handle = static_cast<Datadog::SampleHandle>(_handle);
    Datadog::SampleManager::flush_sample(handle);
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

        // NB, upload() cancels any inflight uploads in order to ensure only
        // one is active at a time.  This simplifies the fork/thread logic.
        // This is usually fine, but when the user specifies a profiling
        // upload interval less than the upload timeout, we have a potential
        // backlog situation which isn't handled.  This is against recommended
        // practice, but it wouldn't be crazy to add a small backlog queue.
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
    Datadog::SampleManager::reset_profile();
}
