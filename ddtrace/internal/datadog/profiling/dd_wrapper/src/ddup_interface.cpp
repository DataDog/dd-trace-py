#include "ddup_interface.hpp"

#include "code_provenance.hpp"
#include "libdatadog_helpers.hpp"
#include "profile.hpp"
#include "sample.hpp"
#include "sample_manager.hpp"
#include "uploader.hpp"
#include "uploader_builder.hpp"

#include <cstdlib>
#include <iostream>
#ifdef _WIN32
#include <io.h>
#else
#include <unistd.h>
#endif
#include <unordered_map>

// State
bool is_ddup_initialized = false; // NOLINT (cppcoreguidelines-avoid-non-const-global-variables)
std::once_flag ddup_init_flag;    // NOLINT (cppcoreguidelines-avoid-non-const-global-variables)

// When a fork is detected, we need to reinitialize this state.
// This handler will be called in the single thread of the child process after the fork
void
ddup_postfork_child()
{
    Datadog::Uploader::postfork_child();
    Datadog::SampleManager::postfork_child();
    Datadog::UploaderBuilder::postfork_child();
}

void
ddup_postfork_parent()
{
    Datadog::Uploader::postfork_parent();
}

// Since we don't control the internal state of libdatadog's exporter and we want to prevent state-tearing
// after a `fork()`, we just outright prevent uploads from happening during a `fork()` operation.  Since a
// new upload may be scheduled _just_ as `fork()` is entered, we also need to prevent new uploads from happening.
// This mutex is released in both the child and the parent.
void
ddup_prefork()
{
    Datadog::Uploader::prefork();
}

// Configuration
void
ddup_config_env(std::string_view dd_env) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_env(dd_env);
}

void
ddup_config_service(std::string_view service) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_service(service);
}

void
ddup_config_version(std::string_view version) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_version(version);
}

void
ddup_config_runtime(std::string_view runtime) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_runtime(runtime);
}

void
ddup_set_runtime_id(std::string_view runtime_id) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_runtime_id(runtime_id);
}

void
ddup_config_runtime_version(std::string_view runtime_version) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_runtime_version(runtime_version);
}

void
ddup_config_profiler_version(std::string_view profiler_version) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_profiler_version(profiler_version);
}

void
ddup_config_url(std::string_view url) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_url(url);
}

void
ddup_config_user_tag(std::string_view key, std::string_view val) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_tag(key, val);
}

void
ddup_config_sample_type(unsigned int _type) // cppcheck-suppress unusedFunction
{
    Datadog::SampleManager::add_type(_type);
}

void
ddup_config_max_nframes(int max_nframes) // cppcheck-suppress unusedFunction
{
    Datadog::SampleManager::set_max_nframes(max_nframes);
}

void
ddup_config_timeline(bool enabled) // cppcheck-suppress unusedFunction
{
    Datadog::SampleManager::set_timeline(enabled);
}

void
ddup_config_output_filename(std::string_view output_filename) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_output_filename(output_filename);
}

void
ddup_config_sample_pool_capacity(uint64_t capacity) // cppcheck-suppress unusedFunction
{
    Datadog::SampleManager::set_sample_pool_capacity(capacity);
}

bool
ddup_is_initialized() // cppcheck-suppress unusedFunction
{
    return is_ddup_initialized;
}

void
ddup_start() // cppcheck-suppress unusedFunction
{
    std::call_once(ddup_init_flag, []() {
        // Perform any one-time startup operations
        Datadog::SampleManager::init();

#ifdef _WIN32
        // NOTE: Windows does not have fork(), leaving this empty for now
#else
        // install the ddup_fork_handler for pthread_atfork
        // Right now, only do things in the child _after_ fork
        pthread_atfork(ddup_prefork, ddup_postfork_parent, ddup_postfork_child);
#endif

        // Set the global initialization flag
        is_ddup_initialized = true;
        return true;
    });
}

Datadog::Sample*
ddup_start_sample() // cppcheck-suppress unusedFunction
{
    return Datadog::SampleManager::start_sample();
}

void
ddup_push_walltime(Datadog::Sample* sample, int64_t walltime, int64_t count) // cppcheck-suppress unusedFunction
{

    sample->push_walltime(walltime, count);
}

void
ddup_push_cputime(Datadog::Sample* sample, int64_t cputime, int64_t count) // cppcheck-suppress unusedFunction
{
    sample->push_cputime(cputime, count);
}

void
ddup_push_acquire(Datadog::Sample* sample, int64_t acquire_time, int64_t count) // cppcheck-suppress unusedFunction
{
    sample->push_acquire(acquire_time, count);
}

void
ddup_push_release(Datadog::Sample* sample, int64_t release_time, int64_t count) // cppcheck-suppress unusedFunction
{
    sample->push_release(release_time, count);
}

void
ddup_push_alloc(Datadog::Sample* sample, int64_t size, int64_t count) // cppcheck-suppress unusedFunction
{
    sample->push_alloc(size, count);
}

void
ddup_push_heap(Datadog::Sample* sample, int64_t size) // cppcheck-suppress unusedFunction
{
    sample->push_heap(size);
}

void
ddup_push_gpu_gputime(Datadog::Sample* sample, int64_t time, int64_t count) // cppcheck-suppress unusedFunction
{
    sample->push_gpu_gputime(time, count);
}

void
ddup_push_gpu_memory(Datadog::Sample* sample, int64_t size, int64_t count) // cppcheck-suppress unusedFunction
{
    sample->push_gpu_memory(size, count);
}

void
ddup_push_gpu_flops(Datadog::Sample* sample, int64_t flops, int64_t count) // cppcheck-suppress unusedFunction
{
    sample->push_gpu_flops(flops, count);
}

void
ddup_push_lock_name(Datadog::Sample* sample, std::string_view lock_name) // cppcheck-suppress unusedFunction
{
    sample->push_lock_name(lock_name);
}

void
ddup_push_threadinfo(Datadog::Sample* sample, // cppcheck-suppress unusedFunction
                     int64_t thread_id,
                     int64_t thread_native_id,
                     std::string_view thread_name)
{
    sample->push_threadinfo(thread_id, thread_native_id, thread_name);
}

void
ddup_push_task_id(Datadog::Sample* sample, int64_t task_id) // cppcheck-suppress unusedFunction
{
    sample->push_task_id(task_id);
}

void
ddup_push_task_name(Datadog::Sample* sample, std::string_view task_name) // cppcheck-suppress unusedFunction
{
    sample->push_task_name(task_name);
}

void
ddup_push_span_id(Datadog::Sample* sample, uint64_t span_id) // cppcheck-suppress unusedFunction
{
    sample->push_span_id(span_id);
}

void
ddup_push_local_root_span_id(Datadog::Sample* sample, uint64_t local_root_span_id) // cppcheck-suppress unusedFunction
{
    sample->push_local_root_span_id(local_root_span_id);
}

void
ddup_push_trace_type(Datadog::Sample* sample, std::string_view trace_type) // cppcheck-suppress unusedFunction
{
    sample->push_trace_type(trace_type);
}

void
ddup_push_exceptioninfo(Datadog::Sample* sample, // cppcheck-suppress unusedFunction
                        std::string_view exception_type,
                        int64_t count)
{
    sample->push_exceptioninfo(exception_type, count);
}

void
ddup_push_class_name(Datadog::Sample* sample, std::string_view class_name) // cppcheck-suppress unusedFunction
{
    sample->push_class_name(class_name);
}

void
ddup_push_gpu_device_name(Datadog::Sample* sample, std::string_view gpu_device_name) // cppcheck-suppress unusedFunction
{
    sample->push_gpu_device_name(gpu_device_name);
}

void
ddup_push_frame(Datadog::Sample* sample, // cppcheck-suppress unusedFunction
                std::string_view _name,
                std::string_view _filename,
                uint64_t address,
                int64_t line)
{
    sample->push_frame(_name, _filename, address, line);
}

void
ddup_push_absolute_ns(Datadog::Sample* sample, int64_t timestamp_ns) // cppcheck-suppress unusedFunction
{
    sample->push_absolute_ns(timestamp_ns);
}

void
ddup_push_monotonic_ns(Datadog::Sample* sample, int64_t monotonic_ns) // cppcheck-suppress unusedFunction
{
    sample->push_monotonic_ns(monotonic_ns);
}

void
ddup_flush_sample(Datadog::Sample* sample) // cppcheck-suppress unusedFunction
{
    sample->flush_sample();
}

void
ddup_flush_sample_v2(Datadog::Sample* sample) // cppcheck-suppress unusedFunction
{
    sample->flush_sample(/*reverse_locations*/ true);
}

void
ddup_drop_sample(Datadog::Sample* sample) // cppcheck-suppress unusedFunction
{
    Datadog::SampleManager::drop_sample(sample);
}

bool
ddup_upload(std::string output_filename) // cppcheck-suppress unusedFunction
{
    std::cout << "in ddup_upload() " << getpid() << std::endl;
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!is_ddup_initialized) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "ddup_upload() called before ddup_start()" << std::endl;
        }
        return false;
    }

    if (output_filename.empty()) {
        auto uploader_or_err = Datadog::UploaderBuilder::build();

        if (std::holds_alternative<std::string>(uploader_or_err)) {
            std::cout << "Failed to create uploader: " << std::get<std::string>(uploader_or_err) << std::endl;
            if (!already_warned) {
                already_warned = true;
                std::cerr << "Failed to create uploader: " << std::get<std::string>(uploader_or_err) << std::endl;
            }
            return false;
        }

        // Get the reference to the uploader
        auto& uploader = std::get<Datadog::Uploader>(uploader_or_err);
        // There are a few things going on here.
        // * profile_borrow() takes a reference in a way that locks the areas where the profile might
        //  be modified.  It gets released and cleared after uploading.
        // * Uploading cancels inflight uploads. There are better ways to do this, but this is what
        //   we have for now.
        uploader.upload(Datadog::Sample::profile_borrow());
        Datadog::Sample::profile_release();
        Datadog::Sample::profile_clear_state();
        return true;
    } else {
        Datadog::Uploader::export_to_file(Datadog::Sample::profile_borrow(), output_filename);
        Datadog::Sample::profile_release();
        Datadog::Sample::profile_clear_state();
        return true;
    }
}

void
ddup_profile_set_endpoints(
  std::unordered_map<int64_t, std::string_view> span_ids_to_endpoints) // cppcheck-suppress unusedFunction
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    ddog_prof_Profile& profile = Datadog::Sample::profile_borrow();
    for (const auto& [span_id, trace_endpoint] : span_ids_to_endpoints) {
        ddog_CharSlice trace_endpoint_slice = Datadog::to_slice(trace_endpoint);
        auto res = ddog_prof_Profile_set_endpoint(&profile, span_id, trace_endpoint_slice);
        if (!res.ok) {
            auto err = res.err;
            if (!already_warned) {
                already_warned = true;
                const std::string errmsg = Datadog::err_to_msg(&err, "Error setting endpoint");
                std::cerr << errmsg << std::endl;
            }
            ddog_Error_drop(&err);
        }
    }
    Datadog::Sample::profile_release();
}

void
ddup_profile_add_endpoint_counts(std::unordered_map<std::string_view, int64_t> trace_endpoints_to_counts)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    ddog_prof_Profile& profile = Datadog::Sample::profile_borrow();
    for (const auto& [trace_endpoint, count] : trace_endpoints_to_counts) {
        ddog_CharSlice trace_endpoint_slice = Datadog::to_slice(trace_endpoint);
        auto res = ddog_prof_Profile_add_endpoint_count(&profile, trace_endpoint_slice, count);
        if (!res.ok) {
            auto err = res.err;
            if (!already_warned) {
                already_warned = true;
                const std::string errmsg = Datadog::err_to_msg(&err, "Error adding endpoint count");
                std::cerr << errmsg << std::endl;
            }
            ddog_Error_drop(&err);
        }
    }
    Datadog::Sample::profile_release();
}
