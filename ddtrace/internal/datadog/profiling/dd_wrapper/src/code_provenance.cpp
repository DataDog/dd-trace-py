#include "code_provenance.hpp"

#include <iostream>
#include <string>
#include <string_view>
#include <unistd.h>
namespace Datadog {

std::string_view
Datadog::CodeProvenance::get_json_str()
{
    return json_str;
}

void
Datadog::CodeProvenance::set_json_str(std::string_view _json_str)
{
    std::cout << "set_json_str on " << getpid() << std::endl;
    this->json_str = _json_str;
    std::cout << this->json_str << std::endl;
}

}
