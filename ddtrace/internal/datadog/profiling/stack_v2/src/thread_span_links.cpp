#include "thread_span_links.hpp"

#include <iostream>
#include <mutex>
#include <stdint.h>
#include <string>

namespace Datadog {
void
ThreadSpanLinks::link_span(uint64_t thread_id, uint64_t span_id, uint64_t local_root_span_id, std::string span_type)
{
    std::lock_guard<std::mutex> lock(mtx);

    if (thread_id_to_span.find(thread_id) == thread_id_to_span.end()) {
        thread_id_to_span[thread_id] = std::make_unique<Span>(span_id, local_root_span_id, span_type);
    }
    thread_id_to_span[thread_id]->span_id = span_id;
    thread_id_to_span[thread_id]->local_root_span_id = local_root_span_id;
    thread_id_to_span[thread_id]->span_type = span_type;
}

const Span*
ThreadSpanLinks::get_active_span_from_thread_id(uint64_t thread_id)
{
    std::lock_guard<std::mutex> lock(mtx);

    if (thread_id_to_span.find(thread_id) == thread_id_to_span.end()) {
        return nullptr;
    }
    return thread_id_to_span[thread_id].get();
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
    // Explicitly destroy and reconstruct the mutex to avoid undefined behavior
    get_instance().mtx.~mutex();
    new (&get_instance().mtx) std::mutex();

    get_instance().reset();
}

} // namespace Datadog
