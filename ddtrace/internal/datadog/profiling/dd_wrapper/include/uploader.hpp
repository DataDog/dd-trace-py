#pragma once

#include "profiler_stats.hpp"

#include <string>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

// Uploader handles uploading encoded profiles to the Datadog backend.
// The ddog_prof_ProfileExporter is owned by ProfilerState and reused across
// upload cycles (see ProfilerState::exporter).
// Upload state (lock, cancellation token, sequence number) is stored in the ProfilerState singleton.
class Uploader
{
  private:
    std::string errmsg;
    std::string output_filename;
    ddog_prof_EncodedProfile encoded_profile{};
    Datadog::ProfilerStats profiler_stats;
    std::string process_tags;

    bool export_to_file(ddog_prof_EncodedProfile& encoded, std::string_view internal_metadata_json);

  public:
    bool upload();
    bool upload_unlocked(); // Version that assumes lock is already held
    static void cancel_inflight();
    static void lock();
    static void unlock();

    Uploader(std::string_view _output_filename,
             ddog_prof_EncodedProfile encoded,
             Datadog::ProfilerStats stats,
             std::string_view _process_tags);
    ~Uploader();

    // Disable copy to avoid double-free of encoded_profile.
    Uploader(const Uploader&) = delete;
    Uploader& operator=(const Uploader&) = delete;

    // Move clears the source's encoded_profile so the destructor only drops it once.
    Uploader(Uploader&& other) noexcept
    {
        encoded_profile = other.encoded_profile;
        other.encoded_profile = { .inner = nullptr };
        profiler_stats = other.profiler_stats;
        output_filename = std::move(other.output_filename);
        errmsg = std::move(other.errmsg);
        process_tags = std::move(other.process_tags);
    }

    Uploader& operator=(Uploader&& other) noexcept
    {
        if (this != &other) {
            ddog_prof_EncodedProfile_drop(&encoded_profile);
            encoded_profile = other.encoded_profile;
            other.encoded_profile = { .inner = nullptr };
            profiler_stats = other.profiler_stats;
            output_filename = std::move(other.output_filename);
            errmsg = std::move(other.errmsg);
            process_tags = std::move(other.process_tags);
        }
        return *this;
    }
};

} // namespace Datadog
