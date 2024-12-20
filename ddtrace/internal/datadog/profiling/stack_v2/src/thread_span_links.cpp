#include "thread_span_links.hpp"

#include <iostream>
#include <mutex>
#include <optional>
#include <stdint.h>
#include <string>

namespace Datadog {
void
ThreadSpanLinks::link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type)
{
    std::lock_guard<std::mutex> lock(mtx);

    auto it = thread_id_to_span.find(thread_id);
    if (it == thread_id_to_span.end()) {
        thread_id_to_span[thread_id] = std::make_unique<Span>(span_id, local_root_span_id, span_type);
    } else {
        it->second->span_id = span_id;
        it->second->local_root_span_id = local_root_span_id;
        it->second->span_type = span_type;
    }
}

const std::optional<Span>
ThreadSpanLinks::get_active_span_from_thread_id(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    std::optional<Span> span;
    auto it = thread_id_to_span.find(thread_id);
    if (it != thread_id_to_span.end()) {
        span = *(it->second);
    }
    return span;
}

void
ThreadSpanLinks::unlink_span(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    thread_id_to_span.erase(thread_id); // This is a no-op if the key is not found
}

void
ThreadSpanLinks::reset()
{
    std::lock_guard<std::mutex> lock(mtx);
    thread_id_to_span.clear();
}

void
ThreadSpanLinks::postfork_child()
{
    // NB placement-new to re-init and leak the mutex because doing anything else is UB
    new (&get_instance().mtx) std::mutex();
    get_instance().reset();
}

} // namespace Datadog
