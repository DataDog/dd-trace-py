#include "profiler_stats.hpp"

#include <charconv>
#include <string>

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
Datadog::ProfilerStats::increment_sample_count(size_t k_sample_count)
{
    sample_count += k_sample_count;
}

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

std::string_view
Datadog::ProfilerStats::get_internal_metadata_json()
{
    static std::string result;
    result.reserve(2048);

    result = "{";

    result += R"("sample_count": )";
    append_to_string(result, sample_count);
    result += ",";

    result += R"("sampling_event_count": )";
    append_to_string(result, sampling_event_count);

    result += "}";

    return result;
}
