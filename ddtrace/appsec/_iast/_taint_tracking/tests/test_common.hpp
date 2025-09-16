#pragma once
#include <context/taint_engine_context.h>
#include <gtest/gtest.h>
#include <initializer/initializer.h>
#include <pybind11/embed.h>

namespace py = pybind11;

class PyEnvCheck : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        initializer = make_unique<Initializer>();
        taint_engine_context = make_unique<TaintEngineContext>();
        taint_engine_context->clear_all_request_context_slots();
    }

    void TearDown() override
    {
        if (taint_engine_context) {
            taint_engine_context->clear_all_request_context_slots();
            taint_engine_context.reset();
        }
    }
};

class PyEnvWithContext : public ::testing::Test
{
  protected:
    void SetUp() override
    {
        initializer = make_unique<Initializer>();
        taint_engine_context = make_unique<TaintEngineContext>();
        taint_engine_context->clear_all_request_context_slots();
        // Start a fresh request context slot for tests that depend on a valid map
        context_id = taint_engine_context->start_request_context();
    }

    void TearDown() override
    {
        if (context_id.has_value()) {
            taint_engine_context->finish_request_context(context_id.value());
        }
        taint_engine_context->clear_all_request_context_slots();
        taint_engine_context.reset();
    }

  public:
    std::optional<size_t> context_id;

    PyObject* StringToPyObjectStr(const string& ob) { return PyUnicode_FromString(ob.c_str()); }
    string PyObjectStrToString(PyObject* ob)
    {
        PyObject* utf8_str = PyUnicode_AsEncodedString(ob, "utf-8", "strict");
        const char* res_data = PyBytes_AsString(utf8_str);
        std::string res_string(res_data);
        Py_DecRef(utf8_str);
        return res_string;
    }
    PyObject* StringToPyObjectBytes(const string& ob) { return PyBytes_FromString(ob.c_str()); }
    string PyObjectBytesToString(PyObject* ob)
    {
        const char* res_data = PyBytes_AsString(ob);
        std::string res_string(res_data);
        return res_string;
    }
};

inline void
EXPECT_RANGESEQ(const TaintRangeRefs& r1, const TaintRangeRefs& r2)
{
    if (r1.size() != r2.size()) {
        FAIL() << "Ranges have different sizes: " << r1.size() << " != " << r2.size();
    }

    if (r1.empty() and r2.empty()) {
        return;
    }

    if (&r1 == &r2) {
        return;
    }

    // Iterate over the ranges at r1 and check that they are the same as the range in the same position at r2
    for (size_t i = 0; i < r1.size(); i++) {
        if (r1[i]->start != r2[i]->start) {
            FAIL() << "Ranges have different start values at position " << i << ": " << r1[i]->start
                   << " != " << r2[i]->start;
        }

        if (r1[i]->length != r2[i]->length) {
            FAIL() << "Ranges have different length values at position " << i << ": " << r1[i]->length
                   << " != " << r2[i]->length;
        }

        if (r1[i]->source.name != r2[i]->source.name) {
            FAIL() << "Ranges have different source names at position " << i << ": " << r1[i]->source.name
                   << " != " << r2[i]->source.name;
        }

        if (r1[i]->source.value != r2[i]->source.value) {
            FAIL() << "Ranges have different source values at position " << i << ": " << r1[i]->source.value
                   << " != " << r2[i]->source.value;
        }

        if (r1[i]->source.origin != r2[i]->source.origin) {
            FAIL() << "Ranges have different source origins at position " << i << ": "
                   << origin_to_str(r1[i]->source.origin) << " != " << origin_to_str(r2[i]->source.origin);
        }
    }
}

inline void
EXPECT_RANGESEQ(py::handle o1, py::handle o2)
{
    auto r1 = api_get_ranges(o1);
    auto r2 = api_get_ranges(o2);
    EXPECT_RANGESEQ(r1, r2);
}
