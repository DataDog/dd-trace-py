#include "ddup_interface.hpp"

#include "defer.hpp"
#include "libdatadog_helpers.hpp"
#include "profile_borrow.hpp"
#include "profiler_state.hpp"
#include "uploader.hpp"
#include "uploader_builder.hpp"

#include <iostream>
#include <string_view>
#include <unordered_map>

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

    if (const auto* err = Datadog::error_if_any(uploader_or_err)) {
        if (!already_warned) {
            already_warned = true;
            std::cerr << "Failed to create uploader: " << err->message << std::endl;
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

// Pass by value is intentional: the map may be modified concurrently by other threads,
// so we take a copy to avoid data races while iterating.
void
ddup_profile_set_endpoints(
  // NOLINTNEXTLINE(performance-unnecessary-value-param)
  std::unordered_map<int64_t, std::string_view> span_ids_to_endpoints) // cppcheck-suppress unusedFunction
{
    if (!ddup_is_initialized()) {
        return;
    }

    auto borrowed = Datadog::ProfilerState::get().profile_state.borrow();
    auto& profile = borrowed.profile();
    for (const auto& [span_id, trace_endpoint] : span_ids_to_endpoints) {
        profile.add_endpoint(static_cast<std::uint64_t>(span_id), Datadog::to_rust_str(trace_endpoint));
    }
}

// Pass by value is intentional: the map may be modified concurrently by other threads,
// so we take a copy to avoid data races while iterating.
void
ddup_profile_add_endpoint_counts(
  // NOLINTNEXTLINE(performance-unnecessary-value-param)
  std::unordered_map<std::string_view, int64_t> trace_endpoints_to_counts)
{
    if (!ddup_is_initialized()) {
        return;
    }

    auto borrowed = Datadog::ProfilerState::get().profile_state.borrow();
    auto& profile = borrowed.profile();
    for (const auto& [trace_endpoint, count] : trace_endpoints_to_counts) {
        profile.add_endpoint_count(Datadog::to_rust_str(trace_endpoint), count);
    }
}
