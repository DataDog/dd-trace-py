#include <gtest/gtest.h>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include "context/application_context.h"
#include "context/request_context.h"

namespace py = pybind11;

class ApplicationContextTest : public ::testing::Test {
  protected:
    void SetUp() override {
      // Ensure Python is initialized for each test case scope
      if (!Py_IsInitialized()) {
        py::initialize_interpreter();
        owns_interpreter_ = true;
      }
      if (!application_context) {
        application_context = std::make_unique<ApplicationContext>();
      }
      // Reset maps before each test
      application_context->clear_tainting_maps();
      // Unset native ContextVar id
      py::gil_scoped_acquire gil;
      ensure_iast_ctxvar_created();
      if (g_iast_ctxvar) {
        PyObject* empty = PyUnicode_FromStringAndSize("", 0);
        if (empty) {
          PyObject* token = PyContextVar_Set(g_iast_ctxvar, empty);
          Py_DECREF(empty);
          Py_XDECREF(token);
        } else {
          PyErr_Clear();
        }
      }
    }

    void TearDown() override {
      // Do not finalize interpreter here, the test runner reuses it across tests
    }

  private:
    bool owns_interpreter_ = false;
};

static void set_ctx(const std::string &val) {
  py::gil_scoped_acquire gil;
  ensure_iast_ctxvar_created();
  if (!g_iast_ctxvar) {
    return;
  }
  PyObject* py_s = PyUnicode_FromStringAndSize(val.data(), static_cast<Py_ssize_t>(val.size()));
  if (!py_s) {
    PyErr_Clear();
    return;
  }
  PyObject* token = PyContextVar_Set(g_iast_ctxvar, py_s);
  Py_DECREF(py_s);
  Py_XDECREF(token);
}

TEST_F(ApplicationContextTest, CreateAndGetContextMap) {
  set_ctx("ctx-1");

  // Before creation, map is null
  auto before = application_context->get_context_map();
  ASSERT_EQ(before, nullptr);

  application_context->create_context();
  auto m = application_context->get_context_map();
  ASSERT_NE(m, nullptr);

  // Creating again should not duplicate order
  application_context->create_context();
  auto m2 = application_context->get_context_map();
  ASSERT_EQ(m, m2);
}

TEST_F(ApplicationContextTest, ClearSpecificMap) {
  set_ctx("ctx-2");
  application_context->create_context();
  auto m = application_context->get_context_map();
  ASSERT_NE(m, nullptr);

  application_context->clear_tainting_map(m);
  auto after = application_context->get_context_map();
  ASSERT_EQ(after, nullptr);
}

TEST_F(ApplicationContextTest, EvictsOldestBeyondMaxSize) {
  // Fill MAX_SIZE + 5
  const int total = 4005;
  for (int i = 0; i < total; ++i) {
    set_ctx("ctx-" + std::to_string(i));
    application_context->create_context();
  }
  // Size should be capped at 4000
  ASSERT_LE(application_context->context_maps_size(), static_cast<size_t>(4000));

  // Oldest should be evicted: set to 0 again and expect it's a new map created
  set_ctx("ctx-0");
  auto before = application_context->get_context_map();
  ASSERT_EQ(before, nullptr);
  application_context->create_context();
  auto after = application_context->get_context_map();
  ASSERT_NE(after, nullptr);
}
