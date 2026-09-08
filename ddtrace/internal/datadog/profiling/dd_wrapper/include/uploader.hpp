#pragma once

#include "profiler_stats.hpp"

#include <cstdint>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace Datadog {

// Uploader handles uploading encoded profiles to the Datadog backend.
// Upload state (lock, cancellation token, sequence number) is stored in the ProfilerState singleton.
class Uploader
{
  private:
    std::string errmsg;
    std::string output_filename;
    std::vector<std::uint8_t> encoded_profile{};
    Datadog::ProfilerStats profiler_stats;
    std::string process_tags;

    bool export_to_file(const std::vector<std::uint8_t>& encoded, std::string_view internal_metadata_json);

  public:
    bool upload();
    bool upload_unlocked(); // Version that assumes lock is already held
    static void cancel_inflight();
    static void lock();
    static void unlock();

    Uploader(std::string_view _output_filename,
             std::vector<std::uint8_t> encoded,
             Datadog::ProfilerStats stats,
             std::string_view _process_tags);
    ~Uploader();

    // Disable copy constructor and copy assignment operator.
    Uploader(const Uploader&) = delete;
    Uploader& operator=(const Uploader&) = delete;

    Uploader(Uploader&& other) noexcept
      : errmsg{ std::move(other.errmsg) }
      , output_filename{ std::move(other.output_filename) }
      , encoded_profile{ std::move(other.encoded_profile) }
      , profiler_stats{ other.profiler_stats }
      , process_tags{ std::move(other.process_tags) }
    {
    }

    Uploader& operator=(Uploader&& other) noexcept
    {
        if (this != &other) {
            errmsg = std::move(other.errmsg);
            output_filename = std::move(other.output_filename);
            encoded_profile = std::move(other.encoded_profile);
            profiler_stats = other.profiler_stats;
            process_tags = std::move(other.process_tags);
        }
        return *this;
    }
};

} // namespace Datadog
