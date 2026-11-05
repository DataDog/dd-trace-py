#include "code_provenance.hpp"

#include <mutex>
#include <string>
#include <string_view>

namespace Datadog {

std::string
Datadog::CodeProvenance::get_json_snapshot()
{
    const std::lock_guard lock(json_mutex);
    return json_str;
}

void
Datadog::CodeProvenance::set_json_str(std::string_view _json_str)
{
    const std::lock_guard lock(json_mutex);
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
    json_mutex.postfork_child();
}

}
