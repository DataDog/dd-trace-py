#include <pybind11/pybind11.h>

#include "Source.h"

using namespace std;
namespace py = pybind11;
using namespace pybind11::literals;

Source::Source(string name, string value, OriginType origin)
  : name(std::move(name))
  , value(std::move(value))
  , origin(origin)
{}

Source::Source(int name, string value, const OriginType origin)
  : name(origin_to_str(OriginType{ name }))
  , value(std::move(value))
  , origin(origin)
{}

string
Source::toString() const
{
    ostringstream ret;
    ret << "Source at " << this << " "
        << "[name=" << name << ", value=" << string(value) << ", origin=" << origin_to_str(origin) << "]";
    return ret.str();
}

Source::operator std::string() const
{
    return toString();
}

// Note: don't use size_t or long, if the hash is bigger than an int, Python
// will re-hash it!
int
Source::get_hash() const
{
    return std::hash<size_t>()(std::hash<string>()(name) ^ static_cast<long>(origin) ^ std::hash<string>()(value));
};

void
pyexport_source(py::module& m)
{
    m.def("origin_to_str", &origin_to_str, "origin"_a);
    m.def("str_to_origin", &str_to_origin, "origin"_a);
    py::enum_<TagMappingMode>(m, "TagMappingMode")
      .value("Normal", TagMappingMode::Normal)
      .value("Mapper", TagMappingMode::Mapper)
      .value("Mapper_Replace", TagMappingMode::Mapper_Replace)
      .export_values();

    py::enum_<OriginType>(m, "OriginType")
      .value("PARAMETER", OriginType::PARAMETER)
      .value("PARAMETER_NAME", OriginType::PARAMETER_NAME)
      .value("HEADER", OriginType::HEADER)
      .value("HEADER_NAME", OriginType::HEADER_NAME)
      .value("PATH", OriginType::PATH)
      .value("BODY", OriginType::BODY)
      .value("QUERY", OriginType::QUERY)
      .value("PATH_PARAMETER", OriginType::PATH_PARAMETER)
      .value("COOKIE", OriginType::COOKIE)
      .value("COOKIE_NAME", OriginType::COOKIE_NAME)
      .value("GRPC_BODY", OriginType::GRPC_BODY)
      .export_values();

    py::class_<Source>(m, "Source")
      .def(py::init<string, string, const OriginType>(), "name"_a = "", "value"_a = "", "origin"_a = OriginType())
      .def(py::init<int, string, const OriginType>(), "name"_a = "", "value"_a = "", "origin"_a = OriginType())
      .def_readonly("name", &Source::name)
      .def_readonly("origin", &Source::origin)
      .def_readonly("value", &Source::value)
      .def("to_string", &Source::toString)
      .def("__hash__",
           [](const Source& self) {
               return hash<string>{}(self.name + self.value) * (33 + static_cast<int>(self.origin));
           })
      .def("__str__", &Source::toString)
      .def("__repr__", &Source::toString)
      .def("__eq__", [](const Source* self, const Source* other) {
          if (other == nullptr)
              return false;
          return self->name == other->name && self->origin == other->origin && self->value == other->value;
      });
}
