#pragma once

#include "libdatadog_helpers.hpp"
#include "profiler_stats.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <utility>

namespace Datadog {

// Uploader handles uploading encoded profiles to the Datadog backend.
// Upload state (lock, cancellation token, sequence number) is stored in the ProfilerState singleton.
class Uploader
{
  private:
    std::string output_filename;
    std::optional<rust::Box<ddprof::ProfileExporter>> profile_exporter{};
    std::optional<rust::Box<ddprof::EncodedProfile>> encoded_profile{};
    Datadog::ProfilerStats profiler_stats;
    std::string process_tags;

    bool export_to_file(const ddprof::EncodedProfile& encoded, std::string_view internal_metadata_json);

  public:
    bool upload_unlocked(); // Version that assumes lock is already held
    static void cancel_inflight();
    static void lock();
    static void unlock();

    Uploader(std::string_view _output_filename,
             rust::Box<ddprof::ProfileExporter> profile_exporter,
             rust::Box<ddprof::EncodedProfile> encoded,
             Datadog::ProfilerStats stats,
             std::string_view _process_tags);
    ~Uploader();

    // Disable copy constructor and copy assignment operator.
    Uploader(const Uploader&) = delete;
    Uploader& operator=(const Uploader&) = delete;

    Uploader(Uploader&&) noexcept = default;
    Uploader& operator=(Uploader&& other) noexcept
    {
        if (this != &other) {
            if (profile_exporter.has_value()) {
                cancel_inflight();
            }
            output_filename = std::move(other.output_filename);
            profile_exporter = std::move(other.profile_exporter);
            encoded_profile = std::move(other.encoded_profile);
            profiler_stats = other.profiler_stats;
            process_tags = std::move(other.process_tags);
        }
        return *this;
    }
};

} // namespace Datadog
