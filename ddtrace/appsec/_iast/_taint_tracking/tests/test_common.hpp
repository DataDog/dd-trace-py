#pragma once
#include <Initializer/Initializer.h>
#include <gtest/gtest.h>
#include <pybind11/embed.h>

namespace py = pybind11;

class PyEnvCheck : public ::testing::Test
{
  protected:
    void SetUp() override { py::initialize_interpreter(); }

    void TearDown() override { py::finalize_interpreter(); }
};

class PyEnvWithContext : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        initializer = make_unique<Initializer>();
        py::initialize_interpreter();
        initializer->create_context();
    }

    void TearDown() override
    {
        initializer->reset_contexts();
        initializer.reset();
        py::finalize_interpreter();
    }
public:
    PyObject* StringToPyObjectStr(const string& ob)
    {
        return PyUnicode_FromString(ob.c_str());
    }
    string PyObjectStrToString(PyObject*  ob)
    {
        PyObject* utf8_str = PyUnicode_AsEncodedString(ob, "utf-8", "strict");
        const char* res_data = PyBytes_AsString(utf8_str);
        std::string res_string(res_data);
        Py_DecRef(utf8_str);
        return res_string;
    }
    PyObject* StringToPyObjectBytes(const string& ob)
    {
        return PyBytes_FromString(ob.c_str());
    }
    string PyObjectBytesToString(PyObject*  ob)
    {
        const char* res_data = PyBytes_AsString(ob);
        std::string res_string(res_data);
        return res_string;
    }
};
