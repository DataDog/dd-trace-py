#include <gtest/gtest.h>
#include <pybind11/embed.h>
#include <pybind11/pybind11.h>

#include "context/application_context.h"

namespace py = pybind11;

class ApplicationContextTest : public ::testing::Test {
  protected:
    void SetUp() override {
      if (!Py_IsInitialized()) {
        py::initialize_interpreter();
      }
      application_context = std::make_unique<ApplicationContext>();
      application_context->clear_tainting_maps();
    }
};

TEST_F(ApplicationContextTest, CreateAndGetContextMap) {
  // Before creation, map is null
  auto before = application_context->get_contexts_array();
  ASSERT_EQ(before, nullptr);

  // Create context map
  auto m_created = application_context->create_context_map();
  ASSERT_NE(m_created, nullptr);

  auto m = application_context->get_contexts_array();
  ASSERT_NE(m, nullptr);
  ASSERT_EQ(m, m_created);

  // Creating again should return same map while active
  auto m2 = application_context->create_context_map();
  ASSERT_EQ(m, m2);
}

TEST_F(ApplicationContextTest, ClearSpecificMap) {
  auto m_created = application_context->create_context_map();
  ASSERT_NE(m_created, nullptr);
  auto m = application_context->get_contexts_array();
  ASSERT_NE(m, nullptr);

  application_context->clear_tainting_map(m);
  auto after = application_context->get_contexts_array();
  ASSERT_EQ(after, nullptr);
}

TEST_F(ApplicationContextTest, ResetIsIdempotentAndReusesSlot) {
  auto m1 = application_context->create_context_map();
  ASSERT_NE(m1, nullptr);
  application_context->reset_context();
  application_context->reset_context(); // idempotent
  auto none = application_context->get_contexts_array();
  ASSERT_EQ(none, nullptr);
  auto m2 = application_context->create_context_map();
  ASSERT_NE(m2, nullptr);
}
