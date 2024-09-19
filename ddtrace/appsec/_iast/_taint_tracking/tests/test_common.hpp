#pragma once
#include <Initializer/Initializer.h>
#include <Utils/GenericUtils.h>
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
        initializer->reset_context();
        initializer.reset();
        py::finalize_interpreter();
    }
};
