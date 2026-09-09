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
    std::string errmsg;
    std::string output_filename;
    std::optional<rust::Box<ddprof::ProfileExporter>> ddog_exporter{};
    std::optional<rust::Box<ddprof::EncodedProfile>> encoded_profile{};
    Datadog::ProfilerStats profiler_stats;
    std::string process_tags;
    bool owns_upload_state{ true };

    bool export_to_file(const ddprof::EncodedProfile& encoded, std::string_view internal_metadata_json);

  public:
    bool upload();
    bool upload_unlocked(); // Version that assumes lock is already held
    static void cancel_inflight();
    static void lock();
    static void unlock();

    Uploader(std::string_view _output_filename,
             rust::Box<ddprof::ProfileExporter> ddog_exporter,
             rust::Box<ddprof::EncodedProfile> encoded,
             Datadog::ProfilerStats stats,
             std::string_view _process_tags);
    ~Uploader();

    // Disable copy constructor and copy assignment operator.
    Uploader(const Uploader&) = delete;
    Uploader& operator=(const Uploader&) = delete;

    Uploader(Uploader&& other) noexcept
      : errmsg{ std::move(other.errmsg) }
      , output_filename{ std::move(other.output_filename) }
      , ddog_exporter{ std::move(other.ddog_exporter) }
      , encoded_profile{ std::move(other.encoded_profile) }
      , profiler_stats{ other.profiler_stats }
      , process_tags{ std::move(other.process_tags) }
      , owns_upload_state{ other.owns_upload_state }
    {
        other.owns_upload_state = false;
    }

    Uploader& operator=(Uploader&& other) noexcept
    {
        if (this != &other) {
            if (owns_upload_state) {
                cancel_inflight();
            }
            errmsg = std::move(other.errmsg);
            output_filename = std::move(other.output_filename);
            ddog_exporter = std::move(other.ddog_exporter);
            encoded_profile = std::move(other.encoded_profile);
            profiler_stats = other.profiler_stats;
            process_tags = std::move(other.process_tags);
            owns_upload_state = other.owns_upload_state;
            other.owns_upload_state = false;
        }
        return *this;
    }
};

} // namespace Datadog
