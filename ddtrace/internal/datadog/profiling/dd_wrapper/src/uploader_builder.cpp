#include "uploader_builder.hpp"

#include "libdatadog_helpers.hpp"
#include "profiler_state.hpp"
#include "sample.hpp"

#include <exception>
#include <numeric>
#include <string>
#include <string_view>
#include <unistd.h>
#include <utility>
#include <vector>

void
Datadog::UploaderBuilder::set_env(std::string_view _dd_env)
{
    if (!_dd_env.empty()) {
        ProfilerState::get().dd_env = _dd_env;
    }
}

void
Datadog::UploaderBuilder::set_service(std::string_view _service)
{
    if (!_service.empty()) {
        ProfilerState::get().service = _service;
    }
}

void
Datadog::UploaderBuilder::set_version(std::string_view _version)
{
    if (!_version.empty()) {
        ProfilerState::get().version = _version;
    }
}

void
Datadog::UploaderBuilder::set_runtime(std::string_view _runtime)
{
    if (!_runtime.empty()) {
        ProfilerState::get().runtime = _runtime;
    }
}

void
Datadog::UploaderBuilder::set_runtime_id(std::string_view _runtime_id)
{
    if (!_runtime_id.empty()) {
        ProfilerState::get().runtime_id = _runtime_id;
    }
}

void
Datadog::UploaderBuilder::set_process_id()
{
    auto pid = getpid();
    ProfilerState::get().process_id = std::to_string(pid);
}

void
Datadog::UploaderBuilder::set_runtime_version(std::string_view _runtime_version)
{
    if (!_runtime_version.empty()) {
        ProfilerState::get().runtime_version = _runtime_version;
    }
}

void
Datadog::UploaderBuilder::set_profiler_version(std::string_view _profiler_version)
{
    if (!_profiler_version.empty()) {
        ProfilerState::get().profiler_version = _profiler_version;
    }
}

void
Datadog::UploaderBuilder::set_url(std::string_view _url)
{
    if (!_url.empty()) {
        ProfilerState::get().url = _url;
    }
}

void
Datadog::UploaderBuilder::set_tag(std::string_view _key, std::string_view _val)
{
    if (!_key.empty() && !_val.empty()) {
        ProfilerState::get().user_tags[std::string(_key)] = std::string(_val);
    }
}

void
Datadog::UploaderBuilder::set_process_tags(std::string_view p_tags)
{
    if (!p_tags.empty()) {
        ProfilerState::get().process_tags = p_tags;
    }
}

void
Datadog::UploaderBuilder::set_output_filename(std::string_view _output_filename)
{
    if (!_output_filename.empty()) {
        ProfilerState::get().output_filename = _output_filename;
    }
}

void
Datadog::UploaderBuilder::set_max_timeout_ms(uint64_t _max_timeout_ms)
{
    ProfilerState::get().max_timeout_ms = _max_timeout_ms;
}

std::string
join(const std::vector<std::string>& vec, const std::string& delim)
{
    return std::accumulate(vec.begin(),
                           vec.end(),
                           std::string(),
                           [&delim](const std::string& left, const std::string& right) -> std::string {
                               // If the left and right operands are empty, we don't want to add a delimiter
                               if (left.empty()) {
                                   return right;
                               }
                               if (right.empty()) {
                                   return left;
                               }
                               return left + delim + right;
                           });
}

std::variant<Datadog::Uploader, std::string>
Datadog::UploaderBuilder::build()
{
    auto& state = ProfilerState::get();

    if (state.output_filename.empty()) {
        return "CXX R&D profile path currently supports output_filename/file export only; "
               "agent upload requires CXX encoded-profile split support";
    }

    // Perform profile encoding before creating the Uploader.
    // Also take the Profiler Stats and reset the one being written to.
    std::vector<std::uint8_t> encoded;
    Datadog::ProfilerStats stats;
    {
        // Only keep the lock for the duration of the encoding operation.
        auto borrowed = state.profile_state.borrow();

        // Swap the ProfilerStats (which replaces the one being written to with an empty state).
        // We do this first as we still want to reset ProfilerStats if the serialization fails.
        std::swap(stats, borrowed.stats());
        borrowed.stats().copy_fast_copy_metadata_from(stats);

        try {
            encoded = borrowed.serialize_to_vec();
        } catch (const std::exception& err) {
            return std::string("Error serializing CXX profile: ") + err.what();
        }
    }

    return std::variant<Datadog::Uploader, std::string>{
        std::in_place_type<Datadog::Uploader>, state.output_filename, std::move(encoded), stats, state.process_tags
    };
}
