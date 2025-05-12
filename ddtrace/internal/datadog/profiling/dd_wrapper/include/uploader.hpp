#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <atomic>
#include <memory>
#include <mutex>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

class Uploader
{
  private:
    static inline std::mutex upload_lock{};
    std::string errmsg;
    static inline ddog_CancellationToken cancel{ .inner = nullptr };
    static inline std::atomic<uint64_t> upload_seq{ 0 };
    std::string output_filename;
    ddog_prof_ProfileExporter ddog_exporter{ .inner = nullptr };

    bool export_to_file(ddog_prof_EncodedProfile* encoded);

  public:
    bool upload(ddog_prof_Profile& profile);
    static void cancel_inflight();
    static void lock();
    static void unlock();
    static void prefork();
    static void postfork_parent();
    static void postfork_child();

    Uploader(std::string_view _url, ddog_prof_ProfileExporter ddog_exporter);
    ~Uploader()
    {
        // We need to call _drop() on the exporter and the cancellation token,
        // as their inner pointers are allocated on the Rust side. And since
        // there could be a request in flight, we first need to cancel it. Then,
        // we drop the exporter and the cancellation token. We drop the exporter
        // first, as it uses the cancellation token, to avoid a race condition.
        ddog_CancellationToken_cancel(&cancel);
        ddog_prof_Exporter_drop(&ddog_exporter);
        ddog_CancellationToken_drop(&cancel);
    }

    // Disable copy constructor and copy assignment operator to avoid double-free
    // of ddog_exporter
    Uploader(const Uploader&) = delete;
    Uploader& operator=(const Uploader&) = delete;

    // In move constructor and move assignment operator, we clear inner pointer
    // of ddog_exporter in other to avoid double-free from the destructor.
    Uploader(Uploader&& other) noexcept
    {
        ddog_exporter = other.ddog_exporter;
        other.ddog_exporter = { .inner = nullptr };
        output_filename = std::move(other.output_filename);
        errmsg = std::move(other.errmsg);
    }

    Uploader& operator=(Uploader&& other) noexcept
    {
        if (this != &other) {
            ddog_prof_Exporter_drop(&ddog_exporter);
            ddog_exporter = other.ddog_exporter;
            other.ddog_exporter = { .inner = nullptr };
            output_filename = std::move(other.output_filename);
            errmsg = std::move(other.errmsg);
        }
        return *this;
    }
};

} // namespace Datadog
