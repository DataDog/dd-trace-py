#include "code_provenance_interface.hpp"

#include "code_provenance.hpp"

#include <string_view>

void
code_provenance_set_file_path(std::string_view file_path)
{
    Datadog::CodeProvenance::get_instance().set_file_path(file_path);
}
