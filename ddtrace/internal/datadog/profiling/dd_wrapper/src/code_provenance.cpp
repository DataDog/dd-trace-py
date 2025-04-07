#include "code_provenance.hpp"

#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace Datadog {

std::string_view
Datadog::CodeProvenance::get_json_str()
{
    return json_str;
}

void
Datadog::CodeProvenance::set_json_str(std::string_view _json_str)
{
    this->json_str = _json_str;
}

}
