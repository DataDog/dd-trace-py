#include "ddup_interface.hpp"

#include "defer.hpp"
#include "gc_monitor.hpp"
#include "libdatadog_helpers.hpp"
#include "profile_borrow.hpp"
#include "profiler_state.hpp"
#include "profiler_stats.hpp"
#include "sample_manager.hpp"
#include "uploader.hpp"
#include "uploader_builder.hpp"

#include <iostream>
#include <string_view>
#include <unordered_map>

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
ddup_set_process_id() // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_process_id();
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
ddup_config_process_tags(std::string_view process_tags) // cppcheck-suppress unusedFunction
{
    Datadog::UploaderBuilder::set_process_tags(process_tags);
}

void
ddup_config_sample_type(unsigned int _type) // cppcheck-suppress unusedFunction
{
    Datadog::SampleManager::add_type(_type);
}

void
ddup_set_profiler_settings_json(std::string_view settings_json) // cppcheck-suppress unusedFunction
{
    // Store the caller-supplied compact JSON object on ProfilerState
    // (process-global). It is passed verbatim to libdatadog's exporter via
    // the `info` channel on each upload, so we only sanity-check that it
    // looks like a JSON object here. Empty objects and non-object inputs are
    // dropped silently.
    auto& info_json = Datadog::ProfilerState::get().profiler_settings_info_json;
    if (settings_json.size() > 2 && settings_json.front() == '{' && settings_json.back() == '}') {
        info_json.assign(settings_json);
    } else {
        info_json.clear();
    }
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

void
ddup_config_set_max_timeout_ms(uint64_t max_timeout_ms)
{
    Datadog::UploaderBuilder::set_max_timeout_ms(max_timeout_ms);
}

bool
ddup_is_initialized() // cppcheck-suppress unusedFunction
{
    return Datadog::ProfilerState::get().is_initialized();
}

void
ddup_start() // cppcheck-suppress unusedFunction
{
    Datadog::ProfilerState::get().start();
}

void
ddup_cleanup()
{
    Datadog::ProfilerState::get().cleanup();
}

bool
ddup_upload() // cppcheck-suppress unusedFunction
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    if (!ddup_is_initialized()) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "ddup_upload() called before ddup_start()" << std::endl;
        }
        return false;
    }

    // Acquire the upload lock before building the uploader.
    // This ensures that if a fork happens, the prefork handler will wait for us to finish
    // building and uploading before allowing the fork to proceed. This prevents memory
    // allocated during build() from being orphaned in the child process.
    Datadog::Uploader::lock();
    defer
    {
        Datadog::Uploader::unlock();
    };

    // Build the Uploader, which takes care of serializing the Profile and capturing ProfilerStats.
    // This takes a reference in a way that locks the areas where the profile might
    // be modified. It gets cleared and released as soon as serialization is complete (or has failed).
    auto uploader_or_err = Datadog::UploaderBuilder::build();

    if (std::holds_alternative<std::string>(uploader_or_err)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "Failed to create uploader: " << std::get<std::string>(uploader_or_err) << std::endl;
        }
        return false;
    }

    // Get the reference to the uploader
    auto& uploader = std::get<Datadog::Uploader>(uploader_or_err);

    // Upload while holding the lock (encoding has already been done in UploaderBuilder::build)
    // This also cancels inflight uploads. There are better ways to do this, but this is what
    // we have for now.
    bool result = uploader.upload_unlocked();

    return result;
}

void
ddup_start_gc_monitor(uint64_t interval_ms, // cppcheck-suppress unusedFunction
                      int survivor_threshold,
                      int top_n,
                      bool referrers_enabled,
                      int max_depth)
{
    Datadog::GCMonitor::get().start(interval_ms, survivor_threshold, top_n, referrers_enabled, max_depth);
}

void
ddup_stop_gc_monitor() // cppcheck-suppress unusedFunction
{
    Datadog::GCMonitor::get().stop();
}

// Pass by value is intentional: the map may be modified concurrently by other threads,
// so we take a copy to avoid data races while iterating.
void
ddup_profile_set_endpoints(
  // NOLINTNEXTLINE(performance-unnecessary-value-param)
  std::unordered_map<int64_t, std::string_view> span_ids_to_endpoints) // cppcheck-suppress unusedFunction
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    auto borrowed = Datadog::ProfilerState::get().profile_state.borrow();
    ddog_prof_Profile& profile = borrowed.profile();
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
}

// Pass by value is intentional: the map may be modified concurrently by other threads,
// so we take a copy to avoid data races while iterating.
void
ddup_profile_add_endpoint_counts(
  // NOLINTNEXTLINE(performance-unnecessary-value-param)
  std::unordered_map<std::string_view, int64_t> trace_endpoints_to_counts)
{
    static bool already_warned = false; // cppcheck-suppress threadsafety-threadsafety
    auto borrowed = Datadog::ProfilerState::get().profile_state.borrow();
    ddog_prof_Profile& profile = borrowed.profile();
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
}
