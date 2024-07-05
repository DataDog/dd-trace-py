#pragma once

#include "sample.hpp"
#include "types.hpp"

#include <memory>
#include <mutex>

extern "C"
{
#include "datadog/profiling.h"
}

namespace Datadog {

struct DdogCancellationTokenDeleter
{
    void operator()(ddog_CancellationToken* ptr) const;
};

class Uploader
{
  private:
    static inline std::mutex upload_lock{};
    std::string errmsg;
    static inline std::unique_ptr<ddog_CancellationToken, DdogCancellationTokenDeleter> cancel;
    std::string runtime_id;
    std::string url;
    std::string dir;
    std::unique_ptr<ddog_prof_Exporter, DdogProfExporterDeleter> ddog_exporter;
    uint64_t upload_seq = 0;

  public:
    bool upload(ddog_prof_Profile& profile);
    static void cancel_inflight();
    static void lock();
    static void unlock();
    static void prefork();
    static void postfork_parent();
    static void postfork_child();

    Uploader(std::string_view _url, std::string_view _dir, ddog_prof_Exporter* ddog_exporter, uint64_t _upload_seq);
};

} // namespace Datadog
