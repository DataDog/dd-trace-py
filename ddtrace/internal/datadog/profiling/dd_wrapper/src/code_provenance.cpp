#include "code_provenance.hpp"

#include <fstream>
#include <sstream>
#include <string>
#include <string_view>

namespace Datadog {

std::string_view
Datadog::CodeProvenance::get_json_str()
{
    return json_str;
}

void
Datadog::CodeProvenance::set_file_path(std::string_view file_path)
{
    std::ifstream ifs{ std::string(file_path) };
    if (!ifs.is_open()) {
        return;
    }
    std::ostringstream oss;
    oss << ifs.rdbuf();
    this->json_str = oss.str();
}

}
