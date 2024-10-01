#pragma once

#include <memory>
#include <mutex>
#include <stdint.h>
#include <string>
#include <unordered_map>

namespace Datadog {

struct Span
{
    uint64_t span_id;
    uint64_t local_root_span_id;
    std::string span_type;

    Span(uint64_t _span_id, uint64_t _local_root_span_id, std::string _span_type)
      : span_id(_span_id)
      , local_root_span_id(_local_root_span_id)
      , span_type(_span_type)
    {
    }
};

class ThreadSpanLinks
{
  public:
    static ThreadSpanLinks& get_instance()
    {
        static ThreadSpanLinks instance;
        return instance;
    }

    // Delete Copy constructor and assignment operator to prevent copies
    ThreadSpanLinks(ThreadSpanLinks const&) = delete;
    ThreadSpanLinks& operator=(ThreadSpanLinks const&) = delete;

    void link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type);

    const Span* get_active_span_from_thread_id(uint64_t thread_id);

    static void postfork_child();

  private:
    std::mutex mtx;
    std::unordered_map<uint64_t, std::unique_ptr<Span>> thread_id_to_span;

    // Private Constructor/Destructor
    ThreadSpanLinks() = default;
    ~ThreadSpanLinks() = default;

    void reset();
};

}
