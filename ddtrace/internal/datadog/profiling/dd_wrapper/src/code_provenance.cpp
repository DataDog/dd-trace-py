#include "code_provenance.hpp"

#include <mutex>
#include <new>
#include <pthread.h>
#include <string>
#include <string_view>

namespace Datadog {

CodeProvenance::CodeProvenance()
{
    pthread_atfork([] { CodeProvenance::get_instance().prefork(); },
                   [] { CodeProvenance::get_instance().postfork_parent(); },
                   [] { CodeProvenance::get_instance().postfork_child(); });
}

std::string
Datadog::CodeProvenance::get_json_snapshot()
{
    const std::lock_guard<std::mutex> lock(json_mutex);
    return json_str;
}

void
Datadog::CodeProvenance::set_json_str(std::string_view _json_str)
{
    const std::lock_guard<std::mutex> lock(json_mutex);
    this->json_str = _json_str;
}

void
Datadog::CodeProvenance::prefork()
{
    json_mutex.lock();
}

void
Datadog::CodeProvenance::postfork_parent()
{
    json_mutex.unlock();
}

void
Datadog::CodeProvenance::postfork_child()
{
    new (&json_mutex) std::mutex();
}

}
