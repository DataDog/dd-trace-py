#include "profiler_stats.hpp"

#include <charconv>

namespace {

void
append_to_string(std::string& s, size_t value)
{
    char buf[128];
    auto [ptr, ec] = std::to_chars(std::begin(buf), std::end(buf), value);
    s.append(buf, ptr);
}

} // namespace

void
Datadog::ProfilerStats::increment_sampling_event_count(size_t k_sampling_event_count)
{
    sampling_event_count += k_sampling_event_count;
}

size_t
Datadog::ProfilerStats::get_sampling_event_count()
{
    return sampling_event_count;
}

void
Datadog::ProfilerStats::increment_sample_count(size_t k_sample_count)
{
    sample_count += k_sample_count;
}

size_t
Datadog::ProfilerStats::get_sample_count()
{
    return sample_count;
}

void
Datadog::ProfilerStats::reset_state()
{
    sample_count = 0;
    sampling_event_count = 0;
    profile_start = std::nullopt;
    profile_end = std::nullopt;
}

void
Datadog::ProfilerStats::set_profile_start_if_unset()
{
    if (!profile_start) {
        profile_start = std::chrono::steady_clock::now();
    }
}

void
Datadog::ProfilerStats::set_profile_end()
{
    profile_end = std::chrono::steady_clock::now();
}

std::optional<std::chrono::duration<unsigned long long, std::nano>>
Datadog::ProfilerStats::get_profile_duration()
{
    if (profile_start && profile_end) {
        return *profile_end - *profile_start;
    }

    return std::nullopt;
}

std::string
Datadog::ProfilerStats::get_internal_metadata_json()
{
    std::string internal_metadata_json;
    internal_metadata_json.reserve(128);

    internal_metadata_json += "{";

    auto maybe_profile_duration = get_profile_duration();
    if (maybe_profile_duration) {
        internal_metadata_json += R"("profile_duration_ns": )";
        append_to_string(internal_metadata_json, maybe_profile_duration->count());
        internal_metadata_json += ",";
    }

    internal_metadata_json += R"("sample_count": )";
    append_to_string(internal_metadata_json, sample_count);
    internal_metadata_json += ",";

    internal_metadata_json += R"("sampling_event_count": )";
    append_to_string(internal_metadata_json, sampling_event_count);

    internal_metadata_json += "}";

    return internal_metadata_json;
}
