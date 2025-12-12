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
}

std::string
Datadog::ProfilerStats::get_internal_metadata_json()
{
    std::string internal_metadata_json;
    internal_metadata_json.reserve(128);

    internal_metadata_json += "{";

    internal_metadata_json += R"("sample_count": )";
    append_to_string(internal_metadata_json, sample_count);
    internal_metadata_json += ",";

    internal_metadata_json += R"("sampling_event_count": )";
    append_to_string(internal_metadata_json, sampling_event_count);

    internal_metadata_json += "}";

    return internal_metadata_json;
}
