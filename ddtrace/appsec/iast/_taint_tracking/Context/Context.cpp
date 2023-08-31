#include "Context.h"

using namespace pybind11::literals;

void
pyexport_context(py::module& m)
{
    py::class_<Context, shared_ptr<Context>>(m, "Context")
      .def(py::init<>())
      .def("is_request_blocked", &Context::is_request_blocked)
      .def("block_request", &Context::block_request)
      .def("set_propagation", py::overload_cast<bool>(&Context::set_propagation))
      .def("get_propagation", &Context::get_propagation)
      .def("add_sent_blocking_vulnerability_hash",
           &Context::add_sent_blocking_vulnerability_hash,
           "vulnerability"_a,
           "analyzer_name"_a)
      .def("blocking_vulnerability_hash_already_sent",
           &Context::blocking_vulnerability_hash_already_sent,
           "vulnerability"_a,
           "analyzer_name"_a)
      .def("reset_blocking_vulnerability_hashes", &Context::reset_blocking_vulnerability_hashes);
};
Context::Context() {}
